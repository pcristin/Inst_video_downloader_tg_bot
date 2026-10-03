# EXPERIMENT ONLY: not integrated with the bot; no production deployment.
"""Throwaway first-success process coordinator for the latency experiment."""
import asyncio, json, os, pathlib, signal, subprocess, time

def process_snapshot():
    result={}
    for path in pathlib.Path('/proc').glob('[0-9]*/stat'):
        try:
            fields=path.read_text().rsplit(')',1)[1].split()
            result[int(path.parent.name)]=(int(fields[1]),fields[0])
        except (OSError,ValueError,IndexError):pass
    return result

def descendants(pid):
    rows=process_snapshot();found={pid}
    while True:
        new={p for p,(parent,_) in rows.items() if parent in found}
        if new<=found:return found
        found|=new

def live_pids(pids):
    rows=process_snapshot()
    return [p for p in pids if p in rows and rows[p][1]!='Z']

async def stop_worker(process):
    started=time.monotonic();tracked=descendants(process.pid)
    if process.poll() is None:
        try:os.kill(process.pid,signal.SIGTERM)
        except ProcessLookupError:pass
    until=time.monotonic()+3
    while process.poll() is None and time.monotonic()<until:
        tracked|=descendants(process.pid)
        await asyncio.sleep(.02)
    # Nested provider workers start their own sessions; kill tracked descendants too.
    for pid in live_pids(tracked):
        try:os.kill(pid,signal.SIGKILL)
        except ProcessLookupError:pass
    process.wait(timeout=2)
    for _ in range(50):
        if not live_pids(tracked):break
        await asyncio.sleep(.01)
    return {'cleanup_s':round(time.monotonic()-started,4),'remaining_live_processes':len(live_pids(tracked)),'tracked_processes':len(tracked)}

async def first_ready(specs, *, deadline=90):
    """Specs contain branch name, delayed start, command, env and result path."""
    started=time.monotonic();running={};records={};pending=list(specs);winner=None
    try:
        while pending or running:
            now=time.monotonic()-started
            if now>=deadline:break
            # A failed sole branch immediately starts its backup instead of waiting.
            for spec in list(pending):
                if now>=spec['delay'] or (records and not running):
                    path=pathlib.Path(spec['result']);path.unlink(missing_ok=True)
                    p=subprocess.Popen(spec['cmd'],env=spec.get('env'),stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,start_new_session=True)
                    running[spec['name']]=(p,spec,now);pending.remove(spec)
            completed=[]
            for name,(p,spec,launch_s) in list(running.items()):
                if p.poll() is None:continue
                try:result=json.loads(pathlib.Path(spec['result']).read_text())
                except (OSError,ValueError):result={'status':'worker_error','returncode':p.returncode}
                result.update(branch=name,launch_s=round(launch_s,3),observed_s=round(time.monotonic()-started,3))
                records[name]=result;del running[name]
                if result['status']=='ready':completed.append(result)
            if completed:
                winner=min(completed,key=lambda r:r.get('ready_monotonic',float('inf')))
                break
            await asyncio.sleep(.02)
    finally:
        for name,(p,spec,launch_s) in running.items():
            cleanup=await stop_worker(p)
            records[name]={'branch':name,'status':'cancelled' if winner else 'deadline','launch_s':round(launch_s,3),**cleanup}
        for spec in pending:records[spec['name']]={'branch':spec['name'],'status':'not_started'}
    return winner,records,round(time.monotonic()-started,3)
