# EXPERIMENT ONLY: not integrated with the bot; no production deployment.
import asyncio,json,os,pathlib,sys,tempfile,time,unittest
sys.path.insert(0,str(pathlib.Path(__file__).parent))
from race_core import first_ready,live_pids

class RaceTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):self.tmp=tempfile.TemporaryDirectory();self.root=pathlib.Path(self.tmp.name)
    def tearDown(self):self.tmp.cleanup()
    def spec(self,name,sleep,status='ready',delay=0,descendant=False):
        result=self.root/(name+'.json')
        child="import subprocess; subprocess.Popen([sys.executable,'-c','import time; time.sleep(30)'],start_new_session=True); " if descendant else ''
        code="import time,json,sys,pathlib; "+child+f"time.sleep({sleep}); pathlib.Path({str(result)!r}).write_text(json.dumps(dict(status={status!r},ready_monotonic=time.monotonic())))"
        return {'name':name,'delay':delay,'result':str(result),'cmd':[sys.executable,'-c',code]}
    async def test_first_success_cancels_nested_worker(self):
        winner,records,_=await first_ready([self.spec('fast',.2),self.spec('slow',30,descendant=True)])
        self.assertEqual(winner['branch'],'fast');self.assertEqual(records['slow']['remaining_live_processes'],0)
        self.assertGreaterEqual(records['slow']['tracked_processes'],2)
    async def test_failure_does_not_win_and_hedge_starts_early(self):
        winner,records,elapsed=await first_ready([self.spec('bad',.01,'failed'),self.spec('good',.01,delay=10)])
        self.assertEqual(winner['branch'],'good');self.assertLess(elapsed,1)
    async def test_fast_success_avoids_backup(self):
        winner,records,_=await first_ready([self.spec('fast',.01),self.spec('backup',1,delay=5)])
        self.assertEqual(records['backup']['status'],'not_started')
    async def test_all_fail_no_winner(self):
        winner,_,_=await first_ready([self.spec('a',.01,'failed'),self.spec('b',.01,'failed')])
        self.assertIsNone(winner)
    async def test_tied_success_only_one_winner(self):
        winner,records,_=await first_ready([self.spec('a',.05),self.spec('b',.05)])
        sent=[]
        if winner:sent.append(winner['branch'])
        self.assertEqual(len(sent),1)
    async def test_deadline_reaps_workers(self):
        winner,records,_=await first_ready([self.spec('slow',30)],deadline=.05)
        self.assertIsNone(winner);self.assertEqual(records['slow']['remaining_live_processes'],0)
    async def test_external_cancellation_reaps_workers(self):
        spec=self.spec('slow',30);spec['cmd'][2]="import os,pathlib;pathlib.Path("+repr(str(self.root/'pid'))+").write_text(str(os.getpid()));"+spec['cmd'][2]
        task=asyncio.create_task(first_ready([spec]));await asyncio.sleep(.15);task.cancel()
        with self.assertRaises(asyncio.CancelledError):await task
        self.assertFalse(live_pids({int((self.root/'pid').read_text())}))
    async def test_video_and_photo_staging_select_correct_api(self):
        import ast,types
        source=ast.parse(pathlib.Path(__file__).with_name('race_benchmark.py').read_text())
        node=next(n for n in ast.walk(source) if isinstance(n,ast.AsyncFunctionDef) and n.name=='stage')
        calls=[]
        payload=types.SimpleNamespace(file_id='fake',file_size=1)
        class Bot:
            async def send_video(self,**kwargs):calls.append('video');return types.SimpleNamespace(video=payload)
            async def send_photo(self,**kwargs):calls.append('photo');return types.SimpleNamespace(photo=[payload])
        ns=dict(sem=asyncio.Semaphore(2),bot=Bot(),chat=1,output=self.root,MediaItem=lambda **kwargs:kwargs)
        exec(compile(ast.Module(body=[node],type_ignores=[]),'<stage>','exec'),ns)
        await ns['stage'](0,('video','test',{'width':720,'height':1280,'duration':1}))
        await ns['stage'](1,('photo','test',{'width':720,'height':1280}))
        self.assertEqual(calls,['video','photo'])

if __name__=='__main__':unittest.main()
