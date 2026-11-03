#!/usr/bin/env python3
"""Two STF sources -> NestDAQ TimeFrameBuilder -> FileSink.

--live opens the SiTCP endpoints listed in the setup file (config/local/setup.yaml).
Optional --set-hbfstate switches the root HBF on for the capture and off afterwards;
no reset or firmware action. --replay sends saved STF multipart messages instead of
reading boards. All child processes and the Redis instance belong to this invocation.
"""
import argparse,json,math,os,re,socket,subprocess,time,sys
from pathlib import Path
import yaml
ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT/'nestdaq/lib'))
from run_storage import output_path


def wait_sampler_tcp(procs, output, timeout=10):
    """RUNNING is logged before PreRun connects TCP; wait for both connections."""
    keys = ('ReceiverInput', 'HrtdcInput')
    deadline = time.monotonic() + timeout
    while True:
        ready = True
        for key in keys:
            if procs[key].poll() is not None:
                raise RuntimeError(f'{key} exited before TCP readiness')
            text = (output / f'{key}.log').read_text(errors='replace')
            if 'TCP connection error' in text or 'Socket close' in text:
                raise RuntimeError(f'{key} TCP connection failed or closed')
            ready = ready and 'TCP connected' in text
        if ready:
            return
        if time.monotonic() >= deadline:
            raise RuntimeError('sampler TCP readiness timeout; HBF not started')
        time.sleep(.1)


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('output',type=Path)
    p.add_argument('--seconds',type=float,default=10)
    p.add_argument('--run-number',type=int,required=True,help='explicit E88 run number (0..999999)')
    p.add_argument('--data-dir',type=Path,help='live default: storage directory in the setup file; replay default: output/data')
    p.add_argument('--manifest',type=Path,default=ROOT/'config/local/setup.yaml')
    p.add_argument('--set-hbfstate',type=Path,help='set_hbfstate executable; switches the root HBF on and off around the capture (only with --live; the caller checks it is off before)')
    p.add_argument('--prefix',type=Path,default=os.environ.get('NESTDDAQ_PREFIX'))
    mode=p.add_mutually_exclusive_group(required=True)
    mode.add_argument('--replay',nargs=2,metavar=('RECEIVER_MESSAGES','HRTDC_MESSAGES'))
    mode.add_argument('--live',action='store_true',help='read the boards listed in the setup file')
    p.add_argument('--sender',type=Path)
    a=p.parse_args()
    if not a.prefix or a.seconds<=0 or not math.isfinite(a.seconds) or a.output.exists():p.error('prefix required, seconds positive, output must be new')
    a.output=a.output.resolve();a.prefix=a.prefix.resolve()
    setup=yaml.safe_load(a.manifest.read_text())
    if a.set_hbfstate and not a.live:p.error('HBF gate is allowed only in explicit live mode')
    if a.replay and (not a.sender or not a.sender.is_file() or any(not Path(f).is_file() for f in a.replay)):p.error('replay requires two input files and a sender executable')
    roles={'ReceiverSTF':'receiver','HrtdcSTF':'hrtdc'}
    for role in roles.values():
        b=setup['data_sources'][role]
        if not b['endpoint'] or b['endpoint']=='UNBOUND':p.error('unbound endpoint')
    data_dir=a.data_dir or (a.output/'data' if a.replay else Path(setup['shared_timeframe_topology']['storage']['directory']))
    if a.replay and not a.data_dir:
        from run_storage import run_filename
        run_filename(a.run_number)
        if data_dir.exists():p.error('replay data directory exists')
        data_dir.mkdir(parents=True)
    data_file=output_path(data_dir,a.run_number)
    a.output.mkdir(parents=True,exist_ok=bool(a.replay and not a.data_dir))
    env=dict(os.environ);env['LD_LIBRARY_PATH']=f'{a.prefix}/lib64:{a.prefix}/lib:'+env.get('LD_LIBRARY_PATH','')
    procs={};logs={};hbf_attempted=False;hbf_off_attempted=False;report={'mode':'replay' if a.replay else 'live','hardware_control':'Root HBF gate' if a.set_hbfstate else 'none','setup_manifest':str(a.manifest),'hbf_events':[],'run_number':a.run_number,'data_file':str(data_file),'sources':{},'states':{},'commands':{}}
    def launch(key,command):
        logs[key]=open(a.output/f'{key}.log','w')
        procs[key]=subprocess.Popen([str(x) for x in command],stdout=logs[key],stderr=subprocess.STDOUT,env=env,cwd=ROOT)
        report['commands'][key]=[str(x) for x in command]
    def r(*args,db=0):
        return subprocess.check_output([str(a.prefix/'bin/redis-cli'),'-h','127.0.0.1','-p',str(port),'-n',str(db),*map(str,args)],text=True,stderr=subprocess.DEVNULL).strip()
    def state(key):
        text=(a.output/f'{key}.log').read_text(errors='replace')
        found=re.findall(r'\] ([A-Z ]+)---> ([A-Z ]+)',text)
        return found[-1][1].strip() if found else ''
    def drive(command,target,keys=None):
        deadline=time.monotonic()+35;keys=keys or devices
        while time.monotonic()<deadline:
            for key in devices:
                if procs[key].poll() is not None:raise RuntimeError(f'{key} exited ({procs[key].returncode}) before {target}')
            r('PUBLISH','daqctl',json.dumps({'command':'change_state','value':command,'services':['all'],'instances':['all']}))
            for _ in range(10):
                if all(state(key)==target for key in keys):return
                time.sleep(.2)
        raise RuntimeError(f'{command} timeout: '+str({k:state(k) for k in devices}))
    def hbf(state):
        event={'state':state,'time':time.time()};report['hbf_events'].append(event)
        try:
            result=subprocess.run([str(a.set_hbfstate.resolve()),setup['timing_root']['endpoint'],state],capture_output=True,text=True,timeout=10)
            event.update(rc=result.returncode,output=result.stdout+result.stderr)
            if result.returncode or '#E' in event['output']:raise RuntimeError('HBF control failed; no blind retry')
        except Exception as exc:
            event['error']=str(exc);raise
    with socket.socket() as sock:sock.bind(('127.0.0.1',0));port=sock.getsockname()[1]
    redisconf=a.output/'redis.conf'
    redisconf.write_text(f'bind 127.0.0.1\nport {port}\nprotected-mode yes\nsave ""\nappendonly no\ndir {a.output}\n')
    tag=str(os.getpid());devices=['FileSink','TimeFrameBuilder','ReceiverSTF','HrtdcSTF']
    if not a.replay:devices+=['ReceiverInput','HrtdcInput']
    try:
        module=a.prefix/'lib64/redis/modules/redistimeseries.so'
        if not module.is_file():raise RuntimeError('RedisTimeSeries module required by TimeFrameBuilder metrics')
        launch('redis',[a.prefix/'bin/redis-server',redisconf,'--loadmodule',module])
        deadline=time.monotonic()+10
        while time.monotonic()<deadline:
            if procs['redis'].poll() is not None:raise RuntimeError('Redis exited')
            try:
                if r('PING')=='PONG':break
            except subprocess.CalledProcessError:pass
            time.sleep(.1)
        else:raise RuntimeError('Redis not ready')
        r('TS.ADD','tfb-readiness','*',0)
        r('SET','run_info:run_number',a.run_number)
        # One pull socket with two distinct STF peers; no source cross-talk.
        endpoints=[('FileSink','in','pull','connect'),('TimeFrameBuilder','out','push','bind'),('TimeFrameBuilder','in','pull','bind')]
        links=[('TimeFrameBuilder','out','FileSink','in')]
        for service in roles:
            endpoints.append((service,'out','push','connect'));links.append((service,'out','TimeFrameBuilder','in'))
            if not a.replay:
                source=service.replace('STF','Input')
                endpoints.extend([(source,'out','push','bind'),(service,'in','pull','connect')]);links.append((source,'out',service,'in'))
        for svc,ch,typ,method in endpoints:
            r('HSET',f'daq_service:topology:endpoint:{svc}:{ch}','type',typ,'method',method,'autoSubChannel','false','waitForPeerConnection','false')
        for s,c,d,i in links:r('SET',f'daq_service:topology:link:{s}:{c},{d}:{i}','none')
        for svc in devices:r('SETEX',f'daq_service:{svc}:{svc}-{tag}:presence',120,'owned-launcher-placeholder')
        common=['-S',f'<{a.prefix}/lib64','-P','daq_service','-P','parameter_config','--registry-uri',f'tcp://127.0.0.1:{port}/0','--parameter-config-uri',f'tcp://127.0.0.1:{port}/2','--severity','debug','--color','0']
        def command(service,exe,args):return [exe,*common,'--service-name',service,'--id',f'{service}-{tag}',*args]
        launch('FileSink',command('FileSink',a.prefix/'bin/FileSink',['--in-chan-name','in','--prefix',data_file.parent,'--run-number-format','run{:06d}','--run_number',str(a.run_number),'--ext','.dat','--openmode','create','--discard','false','--multipart','false']))
        for group in ['TimeFrameBuilder',f'TimeFrameBuilder-{tag}']:r('HSET','parameters:'+group,'metrics-uri',f'tcp://127.0.0.1:{port}/0',db=2)
        launch('TimeFrameBuilder',command('TimeFrameBuilder',a.prefix/'bin/TimeFrameBuilder',['--in-chan-name','in','--out-chan-name','out','--buffer-timeout','2000','--buffer-depth-limit','10000','--output-incomplete-tf','true','--discard-output','false']))
        for index,(svc,role) in enumerate(roles.items()):
            b=setup['data_sources'][role];report['sources'][svc]={'endpoint':b['endpoint'],'serial':b['serial'],'tdc_type':b['tdc_type_label']}
            if a.replay:
                if not a.sender:p.error('--sender required with --replay')
                launch(svc,command(svc,a.sender.resolve(),['--out-chan-name','out','--messages-in',str(Path(a.replay[index]).resolve())]))
            else:
                launch(svc,command(svc,a.prefix/'bin/STFBuilder',['--in-chan-name','in','--out-chan-name','out','--fem-id','0','--max-hbf','1','--split','0','--time-frame-id-type','0','--discard-output','false']))
                source=svc.replace('STF','Input')
                for group in [source,f'{source}-{tag}']:r('HSET','parameters:'+group,'msiTcpIp',b['endpoint'],'TdcType',b['tdc_type_label'],db=2)
                launch(source,command(source,a.prefix/'bin/AmQStrTdcSampler',['--out-chan-name','out']))
        time.sleep(2)
        for cmd,target in [('INIT DEVICE','INITIALIZED'),('BIND','BOUND'),('CONNECT','DEVICE READY'),('INIT TASK','READY')]:drive(cmd,target)
        tftext=(a.output/'TimeFrameBuilder.log').read_text(errors='replace')
        if not re.search(r'number of source = 2\b',tftext):raise RuntimeError('TimeFrameBuilder did not bind exactly two input peers')
        drive('RUN','RUNNING',keys=['FileSink','TimeFrameBuilder'] if a.replay else devices)
        if not a.replay:
            wait_sampler_tcp(procs, a.output)
        print('RUNNING: two STF peers -> TimeFrameBuilder -> FileSink',flush=True)
        if a.set_hbfstate:
            hbf_attempted=True;hbf('on')
        deadline=time.monotonic()+a.seconds
        while time.monotonic()<deadline:
            for key in devices:
                if procs[key].poll() is not None:raise RuntimeError(f'{key} exited during capture')
            time.sleep(.2)
        if a.set_hbfstate:
            hbf_off_attempted=True;hbf('off');time.sleep(1)
        report['states']={key:state(key) for key in devices}
        r('PUBLISH','daqctl',json.dumps({'command':'change_state','value':'STOP','services':['all'],'instances':['all']}));time.sleep(2)
        r('PUBLISH','daqctl',json.dumps({'command':'change_state','value':'END','services':['all'],'instances':['all']}))
        for key in reversed(devices):
            try:procs[key].wait(timeout=10)
            except subprocess.TimeoutExpired:raise RuntimeError(f'{key} did not end cleanly')
        report['exit_codes']={k:procs[k].returncode for k in devices}
        if any(report['exit_codes'].values()):raise RuntimeError('nonzero device exit')
        report['status']='CAPTURE_COMPLETE'
    except Exception as exc:
        report['status']='FAILED';report['error']=str(exc);raise
    finally:
        # Always try to switch HBF off after any on attempt, even a failed one.
        if hbf_attempted and not hbf_off_attempted:
            try:hbf_off_attempted=True;hbf('off')
            except Exception as exc:report['hbf_restore_error']=str(exc)
        # Only Popen objects created by this process; no process-name signalling.
        for key,proc in reversed(list(procs.items())):
            if proc.poll() is None:
                proc.terminate()
                try:proc.wait(timeout=5)
                except subprocess.TimeoutExpired:proc.kill();proc.wait()
        for log in logs.values():log.close()
        report['final_exit_codes']={k:p.returncode for k,p in procs.items()}
        (a.output/'report.json').write_text(json.dumps(report,indent=2)+'\n')
if __name__=='__main__':main()
