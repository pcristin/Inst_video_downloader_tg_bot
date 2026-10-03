import asyncio,dataclasses,json,logging,shutil,time
from pathlib import Path
from types import SimpleNamespace
logging.disable(logging.CRITICAL)
from telegram import Bot
from telegram.request import HTTPXRequest
from src.instagram_video_bot.config.settings import settings
from src.instagram_video_bot.services.video_downloader import VideoDownloader
from src.instagram_video_bot.services.instagram_delivery_race import prepare_instagram_delivery,drain_race_cleanup
from src.instagram_video_bot.services.telegram_media_stager import TelegramMediaStager
from src.instagram_video_bot.services.telegram_media_sender import TelegramMediaSender
from src.instagram_video_bot.services.telegram.request_context import RequestContext
from src.instagram_video_bot.services.state_store import StateStore
from src.instagram_video_bot.utils.account_manager import get_account_manager
SAMPLES=json.loads(Path('/samples.json').read_text())
async def batch():
 manager=get_account_manager();base=settings.TEMP_DIR
 eligible=sorted([a for a in manager.accounts if not a.is_banned and a.session_file.exists() and manager._ramp_allows(a)],key=lambda a:a.username)
 assert len(eligible)>=2*len(SAMPLES)
 pairs=[eligible[2*i:2*i+2] for i in range(len(SAMPLES))]
 chat=settings.TELEGRAM_MEDIA_STORAGE_CHAT_ID or settings.INLINE_STORAGE_CHAT_ID
 results=[]
 settings.INSTAGRAM_DELIVERY_RACE_ENABLED=True
 for index,sample in enumerate(SAMPLES):
  for mode in (['current','race'] if index%2==0 else ['race','current']):
   pair=pairs[index]
   allowed={a.username for a in (pair if mode=='race' else pair[1:])}
   manager._leased_accounts.clear()
   manager._leased_accounts.update(a.username for a in manager.accounts if a.username not in allowed)
   out={'sample':sample['sample'],'mode':mode,'status':'running'}
   print(json.dumps({'event':'started',**out}),flush=True)
   output=base/f"item-{sample['sample']}-{mode}";output.mkdir(exist_ok=True)
   downloader=VideoDownloader();start=time.perf_counter()
   ctx=RequestContext(request_id=f"integrated-{sample['sample']}-{mode}",chat_id=chat,user_id=0,provider_label='Instagram',normalized_url=sample['url'],original_url=sample['url'],original_message_id=None,status_message=None,quiet_mode=True,joined_existing=False,received_monotonic=start)
   try:
    async with asyncio.timeout(150):
     async with Bot(settings.BOT_TOKEN,base_url=settings.TELEGRAM_BOT_API_BASE_URL,base_file_url=settings.TELEGRAM_BOT_API_BASE_FILE_URL,local_mode=True,request=HTTPXRequest(connection_pool_size=8,read_timeout=40,media_write_timeout=40)) as bot:
      stager=TelegramMediaStager(chat)
      if mode=='race':info=await prepare_instagram_delivery(downloader,sample['url'],output,bot,stager)
      else:info=await downloader.download_video(sample['url'],output)
      info.media_items=await stager.stage_media(bot,info.media_items)
      out['ready_s']=round(time.perf_counter()-start,3)
      out['final_send_invocations']=1
      await TelegramMediaSender(StateStore()).send_media(SimpleNamespace(bot=bot),ctx,info,fallback_to_local_on_rejected_file_id=False)
      out.update(status='delivered',first_media_s=round(ctx.first_media_sent_monotonic-start,3),total_s=round(ctx.all_media_sent_monotonic-start,3),media_count=len(info.media_items),media_types=[i.media_type for i in info.media_items])
      # Keep shared HTTP client alive until cancelled storage requests finish.
      await drain_race_cleanup()
   except Exception as exc:
    out.update(status='failed',error_class=type(exc).__name__,elapsed_s=round(time.perf_counter()-start,3))
   finally:
    await drain_race_cleanup()
    out['drained_s']=round(time.perf_counter()-start,3)
    metrics=dataclasses.asdict(downloader.last_provider_metrics)
    metrics.pop('instagram_fast_endpoint_timings_json',None)
    out['provider_metrics']=metrics
    out['remaining_background_cleanups']=len(getattr(asyncio.get_running_loop(),'_instagram_race_cleanup',[]))
    downloader.instagram_runtime.shutdown()
   results.append(out)
   (base/'integrated-results.json').write_text(json.dumps(results,indent=2))
   print(json.dumps({'event':'finished',**out}),flush=True)
   shutil.rmtree(output,ignore_errors=True)
   await asyncio.sleep(3)
asyncio.run(batch())
