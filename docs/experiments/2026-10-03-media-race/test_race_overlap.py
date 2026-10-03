# EXPERIMENT ONLY: delivery overlaps loser cleanup; not production code.
import asyncio,pathlib,sys,time,unittest
sys.path.insert(0,str(pathlib.Path(__file__).parent))
import test_race_core
from race_core_overlap import first_ready
test_race_core.first_ready=first_ready
RaceTests=test_race_core.RaceTests

class OverlapTests(RaceTests):
    async def test_send_starts_before_slow_cleanup_finishes(self):
        slow=self.spec('slow',30)
        slow['cmd'][2]='import signal;signal.signal(signal.SIGTERM,signal.SIG_IGN);'+slow['cmd'][2]
        start=time.monotonic();sent=[]
        async def send(winner):sent.append(time.monotonic()-start)
        winner,records,elapsed=await first_ready([self.spec('fast',.15),slow],on_winner=send)
        self.assertEqual(len(sent),1);self.assertLess(sent[0],1)
        self.assertGreater(elapsed,3);self.assertEqual(records['slow']['remaining_live_processes'],0)
    async def test_no_commit_when_all_candidates_fail(self):
        sent=[]
        async def send(winner):sent.append(winner)
        winner,_,_=await first_ready([self.spec('bad',.01,'failed')],on_winner=send)
        self.assertIsNone(winner);self.assertEqual(sent,[])

if __name__=='__main__':unittest.main(defaultTest='OverlapTests')
