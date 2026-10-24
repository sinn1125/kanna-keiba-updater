"""Persistent external updater, independent of GitHub Actions schedule.

Prepare one isolated checkout per component. Only this worker's temporary
checkouts are reset; the source/build checkout is never modified. Deploy on a
persistent process host after configuring repository-scoped write credentials.
"""
import argparse
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone, timedelta
import base64
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time

JST = timezone(timedelta(hours=9))
INTERVALS = {'results':120, 'odds':120, 'entries':300,
             'weights':300, 'forecasts':300, 'histories':1800}
REPOSITORY = 'https://github.com/sinn1125/kanna-keiba-updater.git'


def live_window(now):
    local = now.astimezone(JST)
    # Continue through 17:10 to collect late final-race confirmations.
    return 9*60 <= local.hour*60+local.minute <= 17*60+10


def retry_delay(interval, failures):
    return min(900, interval*2**min(failures,3))


def git_environment():
    env = dict(os.environ)
    token = env.get('UPDATER_GITHUB_TOKEN')
    if not token:
        raise ValueError('Configure UPDATER_GITHUB_TOKEN before starting the worker')
    # Process environment only: no token in URLs, files, shell arguments or logs.
    count = int(env.get('GIT_CONFIG_COUNT','0'))
    env['GIT_CONFIG_COUNT'] = str(count+1)
    env[f'GIT_CONFIG_KEY_{count}'] = 'http.https://github.com/.extraheader'
    auth = base64.b64encode(('x-access-token:'+token).encode()).decode()
    env[f'GIT_CONFIG_VALUE_{count}'] = 'AUTHORIZATION: basic '+auth
    env['GIT_TERMINAL_PROMPT'] = '0'
    return env


def git(directory, env, *args):
    subprocess.run(['git',*args], cwd=directory, env=env, check=True,
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=90)


def update(component, directory, env):
    try:
        if not (directory/'.git').exists():
            git(directory.parent, env, 'clone','--quiet','--depth','1',REPOSITORY,str(directory))
        git(directory, env, 'fetch','--quiet','origin','main')
        git(directory, env, 'reset','--quiet','--hard','origin/main')
        result = subprocess.run([sys.executable,'scripts/update-component.py',component,'--publish'],
                                cwd=directory, env=env, timeout=540,
                                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return result.returncode == 0
    except (subprocess.SubprocessError, OSError):
        return False


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--publish',action='store_true',help='Required to enable data writes')
    args=parser.parse_args()
    if not args.publish:
        parser.error('Use --publish only after host, cost, credentials and source access are verified')
    env=git_environment()
    env.update(JRA_FETCH_WORKERS='1',JRA_STAGE_TIMEOUT='360')
    due={part:0 for part in INTERVALS};failures={part:0 for part in INTERVALS};running={}
    with tempfile.TemporaryDirectory(prefix='kanna-live-') as directory, ThreadPoolExecutor(max_workers=2) as pool:
        root=Path(directory)
        while True:
            now=time.monotonic()
            for part,future in list(running.items()):
                if not future.done():continue
                success=future.result();failures[part]=0 if success else failures[part]+1
                due[part]=now+retry_delay(INTERVALS[part],failures[part])
                print(json.dumps({'stage':part,'success':success,'completedAt':datetime.now(JST).isoformat(),
                                  'retryInSeconds':round(due[part]-now)}),flush=True)
                del running[part]
            if live_window(datetime.now(JST)):
                # Prioritize live results and odds; never overlap a component.
                for part in INTERVALS:
                    if len(running)>=2:break
                    if part not in running and now>=due[part]:
                        running[part]=pool.submit(update,part,root/part,env)
            time.sleep(5)


if __name__=='__main__':
    main()
