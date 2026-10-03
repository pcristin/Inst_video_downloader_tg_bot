# EXPERIMENT ONLY: not integrated with the bot; no production deployment.
import asyncio,dataclasses,importlib.metadata,json,logging,os,signal,subprocess,sys,time
from pathlib import Path
from types import SimpleNamespace
logging.disable(logging.CRITICAL)
from telegram import Bot,InputMediaPhoto,InputMediaVideo
from telegram.error import RetryAfter
from telegram.request import HTTPXRequest
from instagrapi.exceptions import LoginRequired,ChallengeRequired,PleaseWaitFewMinutes,FeedbackRequired
from src.instagram_video_bot.config.settings import settings
from src.instagram_video_bot.services.video_downloader import VideoDownloader,_SavedSessionInstagramClient
from src.instagram_video_bot.services.download_models import VideoInfo,MediaItem
from src.instagram_video_bot.services.telegram_media_stager import TelegramMediaStager
from src.instagram_video_bot.services.telegram_media_sender import TelegramMediaSender
from src.instagram_video_bot.services.telegram.request_context import RequestContext
from src.instagram_video_bot.services.state_store import StateStore
from src.instagram_video_bot.utils.account_manager import get_account_manager
SAMPLES=json.loads(Path('/samples.json').read_text())

def checkpoint(path,result):
 path.write_text(json.dumps(result))

def probe(value):
 p=subprocess.run(['ffprobe','-v','error','-rw_timeout','8000000','-show_entries','format=duration:stream=codec_type,codec_name,pix_fmt,width,height','-of','json',str(value)],capture_output=True,timeout=12)
 data=json.loads(p.stdout);streams=data.get('streams',[])
 video=next((s for s in streams if s.get('codec_type')=='video'),{})
 audio=[s.get('codec_name') for s in streams if s.get('codec_type')=='audio']
 return {'width':video.get('width'),'height':video.get('height'),'video_codec':video.get('codec_name'),'pixel_format':video.get('pix_fmt'),'audio_codecs':audio,'duration':float(data.get('format',{}).get('duration') or 0)}

async def trial(mode,index):
 sample=SAMPLES[index];sid=sample['sample'];url=sample['url']
 result={'sample':sid,'url':url,'mode':mode,'status':'running','stage':'setup','instagrapi':importlib.metadata.version('instagrapi')}
 progress=settings.TEMP_DIR/f'progress-{sid}-{mode}.json'
 manager=get_account_manager()
 assigned=json.loads((settings.TEMP_DIR/'sample-accounts.json').read_text())
 # Pinned mapping prevents a failed account from shifting later pair assignments.
 if index>=len(assigned):raise RuntimeError('InsufficientSavedSessions')
 account=next(a for a in manager.accounts if a.username==assigned[index][mode])
 if account.is_banned:
  result.update(status='skipped',reason='account_health_cooldown');return result
 manager._leased_accounts.update(a.username for a in manager.accounts if a.username!=account.username)
 if not manager._ramp_allows(account):
  result.update(status='skipped',reason='account_ramp_cooldown');return result
 downloader=VideoDownloader();store=StateStore();leased=False
 chat=settings.TELEGRAM_MEDIA_STORAGE_CHAT_ID or settings.INLINE_STORAGE_CHAT_ID
 request=HTTPXRequest(connection_pool_size=8,read_timeout=40,media_write_timeout=40)
 # Both timers include bot initialization and account/provider preparation.
 start=time.perf_counter();result['stage']='metadata_or_acquisition';checkpoint(progress,result)
 try:
  async with Bot(settings.BOT_TOKEN,base_url=settings.TELEGRAM_BOT_API_BASE_URL,base_file_url=settings.TELEGRAM_BOT_API_BASE_FILE_URL,local_mode=True,request=request) as bot:
   ctx=RequestContext(request_id=f'benchmark-{sid}-{mode}',chat_id=chat,user_id=0,provider_label='Instagram',normalized_url=url,original_url=url,original_message_id=None,status_message=None,quiet_mode=True,joined_existing=False,received_monotonic=start)
   output=settings.TEMP_DIR/f'item-{sid}-{mode}';output.mkdir(exist_ok=True)
   if mode=='current':
    info=await downloader.download_video(url,output)
    prepared=time.perf_counter();result['prepare_s']=round(prepared-start,3)
    metrics=dataclasses.asdict(downloader.last_provider_metrics);metrics.pop('instagram_fast_endpoint_timings_json',None)
    result['provider_metrics']=metrics;result['bytes']=sum(i.file_path.stat().st_size for i in info.media_items)
    result.update(stage='storage_upload',media_count=len(info.media_items),media_types=[i.media_type for i in info.media_items]);checkpoint(progress,result)
    info.media_items=await TelegramMediaStager(chat).stage_media(bot,info.media_items)
    staged=time.perf_counter();result['storage_s']=round(staged-prepared,3)
   else:
    got=manager.acquire_account(excluded_usernames=set())
    if got is None:raise RuntimeError('AccountNotImmediatelyEligible')
    leased=True
    client=_SavedSessionInstagramClient(username=account.username,password='',totp_secret=None,session_file=account.session_file,proxy=account.proxy)
    if not client._load_session_into_client():raise RuntimeError('SavedSessionUnavailable')
    await downloader._apply_instagram_throttle(account.username)
    async with downloader._instagram_provider_slot():
     pk=client.client.media_pk_from_url(url)
     raw_data=await asyncio.to_thread(client.client.private_request,f'media/{pk}/info/')
    raw=raw_data.get('items',[None])[0]
    if not raw:raise RuntimeError('NoMediaMetadata')
    result['metadata_s']=round(time.perf_counter()-start,3)
    entries=raw.get('carousel_media') if raw.get('media_type')==8 else [raw]
    if not entries:raise RuntimeError('EmptyCarousel')
    sources=[];specs=[]
    for entry in entries:
     if entry.get('media_type')==2:
      source=client._pick_video_url(entry)
      if not source:raise RuntimeError('MissingVideoURL')
      spec=await asyncio.to_thread(probe,source)
      if spec['video_codec']!='h264' or spec['pixel_format']!='yuv420p' or any(c!='aac' for c in spec['audio_codecs']):raise RuntimeError('RequiresConversion')
      sources.append(('video',source,spec));specs.append(spec)
     elif entry.get('media_type')==1:
      candidates=entry.get('image_versions2',{}).get('candidates',[])
      photo=max(candidates,key=lambda v:v.get('width',0)*v.get('height',0))
      spec={'width':photo.get('width'),'height':photo.get('height')}
      sources.append(('photo',photo['url'],spec));specs.append(spec)
     else:raise RuntimeError('UnsupportedMediaType')
    result.update(media_count=len(sources),media_types=[s[0] for s in sources],media_specs=specs)
    prepared=time.perf_counter();result['prepare_s']=round(prepared-start,3);result['stage']='storage_upload';checkpoint(progress,result)
    sem=asyncio.Semaphore(2)
    async def stage(i,source):
     kind,value,spec=source
     async with sem:
      if kind=='video':
       msg=await bot.send_video(chat_id=chat,video=value,width=spec['width'],height=spec['height'],duration=int(spec['duration']),supports_streaming=True,read_timeout=40)
       payload=msg.video
       if payload is None:raise RuntimeError('TelegramReturnedNonVideo')
      else:
       msg=await bot.send_photo(chat_id=chat,photo=value,read_timeout=40);payload=msg.photo[-1]
      return MediaItem(file_path=output/f'unmaterialized-{i}',media_type=kind,telegram_file_id=payload.file_id,width=spec.get('width'),height=spec.get('height')),payload.file_size or 0
    tasks=[asyncio.create_task(stage(i,s)) for i,s in enumerate(sources)]
    try:pairs=await asyncio.gather(*tasks)
    except BaseException:
     for task in tasks:task.cancel()
     await asyncio.gather(*tasks,return_exceptions=True);raise
    items=[p[0] for p in pairs];result['bytes']=sum(p[1] for p in pairs)
    info=VideoInfo(file_path=items[0].file_path,title='Benchmark',media_items=items)
    staged=time.perf_counter();result['storage_s']=round(staged-prepared,3)
   result.update(status='ready',ready_monotonic=time.monotonic(),items=[{**dataclasses.asdict(i),'file_path':str(i.file_path)} for i in info.media_items]);checkpoint(progress,result)
   return result

 except Exception as exc:
  if mode=='direct' and isinstance(exc,(PleaseWaitFewMinutes,FeedbackRequired)):
   manager.record_account_failure(account,'rate_limited')
  elif mode=='direct' and isinstance(exc,(LoginRequired,ChallengeRequired)):
   manager.record_account_failure(account,'auth_challenge')
  result.update(status='failed',error_class=type(exc).__name__,elapsed_s=round(time.perf_counter()-start,3))
  if isinstance(exc,RetryAfter):
   delay=exc.retry_after;result['retry_after_s']=delay.total_seconds() if hasattr(delay,'total_seconds') else float(delay)
  if isinstance(exc,RuntimeError):result['reason']=str(exc)
  if type(exc).__name__=='BadRequest':
   import re
   result['telegram_error']=re.sub(r'https?://\S+','[URL]',str(exc))[:200]
  import traceback
  result['error_frames']=[{'file':Path(f.filename).name,'function':f.name,'line':f.lineno} for f in traceback.extract_tb(exc.__traceback__)[-3:]]
 finally:
  if leased:manager.release_account(account)
  downloader.instagram_runtime.shutdown()
 checkpoint(progress,result);return result

def sanitized(value):
 if isinstance(value,dict):return {k:sanitized(v) for k,v in value.items() if k not in {'items','ready_monotonic'}}
 if isinstance(value,list):return [sanitized(v) for v in value]
 return value

async def run_case(index,mode):
 from race_core import first_ready
 sample=SAMPLES[index];base=settings.TEMP_DIR
 specs=[]
 for branch,delay in ([('current',0)] if mode=='current' else [('direct',0),('current',8 if mode=='hedged' else 0)]):
  result=base/f'result-{branch}.json'
  env={**os.environ,'ACCOUNT_STATE_FILE':str(base/f'accounts-{branch}.json')}
  specs.append({'name':branch,'delay':delay,'result':str(result),'env':env,'cmd':[sys.executable,__file__,'worker',branch,str(index),str(result)]})
 start=time.monotonic();out={'sample':sample['sample'],'url':sample['url'],'mode':mode,'status':'running'}
 winner,records,coordinator_s=await first_ready(specs)
 out.update(branches=sanitized(records),coordinator_s=coordinator_s,branches_started=sum(r['status']!='not_started' for r in records.values()))
 for branch,row in out['branches'].items():
  if row['status'] in {'cancelled','deadline'}:
   progress=base/f"progress-{sample['sample']}-{branch}.json"
   if progress.exists():row['last_progress']=sanitized(json.loads(progress.read_text()))
 if winner is None:
  out.update(status='failed',elapsed_s=round(time.monotonic()-start,3));return out
 out['winner']=winner['branch'];out['winner_ready_s']=round(winner['ready_monotonic']-start,3)
 items=[MediaItem(**{**i,'file_path':Path(i['file_path'])}) for i in winner['items']]
 chat=settings.TELEGRAM_MEDIA_STORAGE_CHAT_ID or settings.INLINE_STORAGE_CHAT_ID
 ctx=RequestContext(request_id=f"race-{sample['sample']}-{mode}",chat_id=chat,user_id=0,provider_label='Instagram',normalized_url=sample['url'],original_url=sample['url'],original_message_id=None,status_message=None,quiet_mode=True,joined_existing=False,received_monotonic=start)
 try:
  async with Bot(settings.BOT_TOKEN,base_url=settings.TELEGRAM_BOT_API_BASE_URL,base_file_url=settings.TELEGRAM_BOT_API_BASE_FILE_URL,local_mode=True,request=HTTPXRequest(connection_pool_size=8,read_timeout=40,media_write_timeout=40)) as bot:
   out['final_send_invocations']=1
   await TelegramMediaSender(StateStore()).send_media(SimpleNamespace(bot=bot),ctx,VideoInfo(file_path=items[0].file_path,title='Race benchmark',media_items=items),fallback_to_local_on_rejected_file_id=False)
  out.update(status='delivered',first_media_s=round(ctx.first_media_sent_monotonic-start,3),total_s=round(ctx.all_media_sent_monotonic-start,3),media_count=len(items),media_types=[i.media_type for i in items])
 except Exception as exc:
  # Never switch branches after an ambiguous/partial final send.
  out.update(status='final_send_failed',error_class=type(exc).__name__,elapsed_s=round(time.monotonic()-start,3))
 return out

async def worker_entry(mode,index):
 task=asyncio.current_task()
 asyncio.get_running_loop().add_signal_handler(signal.SIGTERM,task.cancel)
 return await trial(mode,index)

async def batch():
 import shutil
 manager=get_account_manager();base=settings.TEMP_DIR
 eligible=sorted([a for a in manager.accounts if not a.is_banned and a.session_file.exists() and manager._ramp_allows(a)],key=lambda a:a.username)
 assert len(eligible)>=2*len(SAMPLES)
 assignments=[{'direct':eligible[2*i].username,'current':eligible[2*i+1].username} for i in range(len(SAMPLES))]
 (base/'sample-accounts.json').write_text(json.dumps(assignments))
 for branch in ['direct','current']:shutil.copyfile(base/'accounts_state.json',base/f'accounts-{branch}.json')
 modes=['current','race','hedged'];results=[]
 for index,sample in enumerate(SAMPLES):
  order=modes[index%3:]+modes[:index%3]
  for mode in order:
   print(json.dumps({'event':'started','sample':sample['sample'],'mode':mode}),flush=True)
   for branch in ['current','direct']:(base/f"progress-{sample['sample']}-{branch}.json").unlink(missing_ok=True)
   result=await run_case(index,mode);results.append(result)
   (base/'race-results.json').write_text(json.dumps(results,indent=2))
   print(json.dumps({'event':'finished',**result}),flush=True)
   for branch in ['current','direct']:shutil.rmtree(base/f"item-{sample['sample']}-{branch}",ignore_errors=True)
   await asyncio.sleep(3)

if __name__=='__main__':
 if len(sys.argv)>1 and sys.argv[1]=='worker':
  try:result=asyncio.run(worker_entry(sys.argv[2],int(sys.argv[3])))
  except asyncio.CancelledError:sys.exit(0)
  Path(sys.argv[4]).write_text(json.dumps(result))
 else:asyncio.run(batch())
