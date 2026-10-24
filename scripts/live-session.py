"""Reuse a hosted Actions runner for bounded, independent live refresh cycles."""
import argparse
from datetime import datetime, timezone, timedelta
from pathlib import Path
import subprocess
import sys
import time

ROOT=Path(__file__).resolve().parents[1]
FILES=('results-data.js','jra-odds.json','jra-forecasts.json')
JST=timezone(timedelta(hours=9))


def in_window(now):
    local=now.astimezone(JST)
    return 9*60<=local.hour*60+local.minute<17*60+15


def refresh_snapshots():
    # Only generated data are refreshed. Never reset source or working notes.
    subprocess.run(['git','fetch','--quiet','origin','main'],cwd=ROOT,check=True,timeout=45)
    snapshots={}
    for name in FILES:
        p=subprocess.run(['git','show','origin/main:dist/'+name],cwd=ROOT,check=True,capture_output=True,timeout=30)
        snapshots[name]=p.stdout
    for name,raw in snapshots.items():
        target=ROOT/'dist'/name;temp=target.with_suffix(target.suffix+'.session-tmp')
        temp.write_bytes(raw);temp.replace(target)


def run_session(component, minutes, interval=120):
    deadline=time.monotonic()+minutes*60
    failed=False;cycles=0
    while time.monotonic()<deadline and in_window(datetime.now(JST)):
        started=time.monotonic()
        try:
            refresh_snapshots()
            result=subprocess.run([sys.executable,'scripts/update-component.py',component,'--publish'],
                                  cwd=ROOT,timeout=540)
            failed=failed or result.returncode!=0
        except (subprocess.SubprocessError,OSError) as error:
            failed=True;print(f'LIVE {component}: {type(error).__name__}; retry next cycle',flush=True)
        cycles+=1
        # At least 120 seconds between starts; slow source requests aren't overlapped.
        wait=max(5,interval-(time.monotonic()-started))
        remaining=deadline-time.monotonic()
        if remaining<=0:break
        time.sleep(min(wait,remaining))
    print(f'LIVE {component}: {cycles} cycles; hadFailure={failed}',flush=True)
    return int(failed)


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('component',choices=('results','odds'))
    parser.add_argument('--minutes',type=int,default=30,choices=range(1,31))
    args=parser.parse_args()
    return run_session(args.component,args.minutes)


if __name__=='__main__':raise SystemExit(main())
