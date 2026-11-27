#!/usr/bin/env python3
"""N STF sources -> NestDAQ TimeFrameBuilder -> FileSink.

Every entry of data_sources in the setup file (config/local/setup.yaml) becomes one
AmQStrTdcSampler -> STFBuilder pair; --sources selects a subset in the given order.
--live opens the SiTCP endpoints listed in the setup file.
Optional --set-hbfstate switches the root HBF on for the capture and off afterwards;
no reset or firmware action. --replay sends saved STF multipart messages instead of
reading boards. All child processes and the Redis instance belong to this invocation.
--webctl ADDR:PORT starts daq-webctl there instead of driving the devices: the state
transitions and RUN/STOP are taken from the browser, the root HBF is switched on after
RUN (once every sampler has connected) and off before STOP by nestdaq/scripts/webctl-hook.py,
and the session ends when every device has exited (END in the browser) or on SIGINT/SIGTERM.
The HBF is switched off again whenever the session ends.
--sc-filter-prescale N also runs the SC coincidence filter (online-filter/e88-sc-filter-device) on the
"dqm" side output of the TimeFrameBuilder and writes the kept time frames with a second FileSink to
run<NNNNNN>_sc.dat next to the run file (marks and summary beside it). The run file itself is unchanged;
the TimeFrameBuilder skips the side output when the filter cannot take a time frame.
--prescale-filter N runs the plain prescale (one time frame out of N, content not read) the same way and writes
run<NNNNNN>_ps.dat. One side-output filter per session. The filter reads <output>/run-settings.txt at every RUN
("kind sc|prescale|off", "prescale N"), so its kind and N can be changed between runs (the session page does this);
the files keep the suffix of the session and the summary of each run records the kind and N used.
--analyzer-http ADDR:PORT runs the online analyzer (nda-replay --follow) on the run file being written and serves its
histograms there (THttpServer / JSROOT). It reads the file only, so it cannot slow the DAQ down; a new run file (a new
run number at RUN) gets a new analyzer, and the last one keeps serving until the next run or the end of the session.
--no-file writes no run file: no FileSink is started and the TimeFrameBuilder discards every time frame it cannot send
(--discard-output true). Its error line for every discarded time frame is counted and left out of TimeFrameBuilder.log
(one summary line per minute instead). Side-output filters are skipped (they write files).
With --analyzer-http the online analyzer then reads the "dqm" side output of the TimeFrameBuilder directly
(nda-online-monitor, best effort: the TimeFrameBuilder sends only when the analyzer can take a time frame).
--analyzer-memory-gb G limits the address space of the online analyzer (default 64 GiB, 0: none; no core dump): when it is
reached the analyzer alone stops instead of the kernel's out-of-memory killer choosing a victim on the host.
The TimeFrameBuilder debug lines of incomplete time frames ('#D Output incomplete TF', '#D TFB Fail.') are counted and left out of
TimeFrameBuilder.log in every run.
"""
import argparse,json,math,os,re,resource,shlex,signal,socket,subprocess,threading,time,sys
from pathlib import Path
import yaml
ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT/'nestdaq/lib'))
from run_storage import output_path
from online_analyzer import RunFileFollower, analyzer_command, parse_http, port_free
import run_settings


def service_base(name):
    """Setup key (e.g. 'hrtdc30') -> NestDAQ service stem (e.g. 'Hrtdc30')."""
    parts=[x for x in re.split(r'[^A-Za-z0-9]+',name) if x]
    if not parts:raise ValueError(f'unusable data source name: {name!r}')
    return ''.join(x[0].upper()+x[1:] for x in parts)


DISCARD_LINE='Discarding data'  # TimeFrameBuilder --discard-output: one error line per time frame it could not send
# Debug lines of the TimeFrameBuilder written for every incomplete time frame. With sources that lose heartbeat frames they
# reach millions of lines (5 GB in 8 hours), so they are counted and left out of TimeFrameBuilder.log like DISCARD_LINE.
FLOOD_LINES=('#D Output incomplete TF','#D TFB Fail.')
ANALYZER_MEMORY_GB=64  # address-space limit of the online analyzer; 0 = none


def analyzer_limits(gigabytes):
    """preexec_fn for the online analyzer: lower priority, an address-space limit and no core dump.

    The analyzer may fall behind or fail, the DAQ must not. When it reaches the limit only the analyzer stops (its
    allocation fails); without a limit the kernel's out-of-memory killer chooses a victim on the whole host
    (a 350 GB analyzer was killed that way). No core file: a core of that size would fill the disk.
    """
    def apply():
        os.nice(10)
        if gigabytes>0:
            limit=int(gigabytes*2**30)
            resource.setrlimit(resource.RLIMIT_AS,(limit,limit))
            resource.setrlimit(resource.RLIMIT_CORE,(0,0))
    return apply


def relay_log(stream, log, drop, counts, period=60, clock=time.monotonic):
    """Copy a device's output to its log, leaving out the lines that contain `drop` (a string or a tuple of strings).

    The dropped lines are counted (counts['dropped']) and summarised once per `period` seconds. The pipe is read
    until EOF whatever happens to the log, so the device can never block on a full pipe.
    """
    last=clock();since=0
    drops=(drop,) if isinstance(drop,str) else tuple(drop);label=' | '.join(drops)
    for raw in iter(stream.readline,b''):
        try:
            line=raw.decode(errors='replace')
            if any(d in line for d in drops):
                counts['dropped']+=1;since+=1
            else:
                log.write(line)
            if since and clock()-last>=period:
                log.write(f"[launcher] {since} lines with '{label}' left out in the last {clock()-last:.0f} s (total {counts['dropped']})\n")
                last=clock();since=0
            log.flush()
        except Exception as exc:  # a full disk or a closed log must not stop the reading
            counts['errors']=counts.get('errors',0)+1;counts['last_error']=str(exc)
    try:
        if since:log.write(f"[launcher] {since} lines with '{label}' left out (total {counts['dropped']})\n")
        log.flush()
    except Exception as exc:
        counts['last_error']=str(exc)


def select_sources(setup, names=None):
    """Ordered {STF service: setup key}; names=None keeps the setup order."""
    available=list(setup['data_sources'])
    chosen=available if not names else names
    unknown=[n for n in chosen if n not in available]
    if unknown:raise ValueError(f'unknown data sources: {unknown}')
    if len(set(chosen))!=len(chosen) or not chosen:raise ValueError('data sources must be non-empty and distinct')
    roles={service_base(n)+'STF':n for n in chosen}
    if len(roles)!=len(chosen):raise ValueError('data source names collide after normalisation')
    return roles


def wait_sampler_tcp(procs, output, keys, timeout=10):
    """RUNNING is logged before PreRun connects TCP; wait for every connection."""
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
    mode.add_argument('--replay',nargs='+',metavar='MESSAGES',help='one saved STF message file per selected source, in source order')
    mode.add_argument('--live',action='store_true',help='read the boards listed in the setup file')
    p.add_argument('--sender',type=Path)
    p.add_argument('--sources',nargs='+',metavar='NAME',help='data_sources keys to use, in order (default: all, in setup order)')
    p.add_argument('--webctl',metavar='ADDR:PORT',help='hand control to daq-webctl listening here (for example 127.0.0.1:8080); --seconds is ignored')
    p.add_argument('--sc-filter-prescale',type=int,metavar='N',help='run the SC coincidence filter on the dqm output; N: unbiased prescale (keep 1 of N time frames regardless of content, N >= 1)')
    p.add_argument('--e88-param',type=Path,help='E88 parameter index for the SC filter (required with --sc-filter-prescale)')
    p.add_argument('--prescale-filter',type=int,metavar='N',help='run the plain prescale filter on the dqm output: keep 1 of N time frames (N >= 1), run<NNNNNN>_ps.dat')
    p.add_argument('--sc-filter-exe',type=Path,default=ROOT/'build/online-filter/bin/e88-sc-filter-device',help='filter device executable (both kinds)')
    p.add_argument('--analyzer-http',metavar='ADDR:PORT',help='run the online analyzer on the run file and serve its histograms here (for example 127.0.0.1:18090); uses --e88-param when given')
    p.add_argument('--analyzer-exe',type=Path,default=ROOT/'build/online-analyzer/bin/nda-replay',help='online analyzer executable (nda-replay)')
    p.add_argument('--monitor-exe',type=Path,default=ROOT/'build/online-analyzer/bin/nda-online-monitor',help='online analyzer device for --no-file (nda-online-monitor)')
    p.add_argument('--analyzer-memory-gb',type=float,default=ANALYZER_MEMORY_GB,help='address-space limit of the online analyzer in GiB (0: none); when it is reached only the analyzer stops')
    p.add_argument('--no-file',action='store_true',help='write no run file (no FileSink); with --analyzer-http the analyzer reads the dqm side output')
    a=p.parse_args()
    if not a.prefix or a.seconds<=0 or not math.isfinite(a.seconds) or a.output.exists():p.error('prefix required, seconds positive, output must be new')
    a.output=a.output.resolve();a.prefix=a.prefix.resolve()
    setup=yaml.safe_load(a.manifest.read_text())
    if a.set_hbfstate and not a.live:p.error('HBF gate is allowed only in explicit live mode')
    try:roles=select_sources(setup,a.sources)
    except ValueError as exc:p.error(str(exc))
    if a.replay and (len(a.replay)!=len(roles) or not a.sender or not a.sender.is_file() or any(not Path(f).is_file() for f in a.replay)):p.error('replay requires one input file per selected source and a sender executable')
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
    if a.sc_filter_prescale is not None and a.prescale_filter is not None:p.error('one side-output filter per session: --sc-filter-prescale or --prescale-filter')
    sc_filter=a.sc_filter_prescale is not None or a.prescale_filter is not None
    skipped_filter=None
    if a.no_file and sc_filter:
        # The filters write files of their own and use the dqm output that the analyzer takes in this mode.
        skipped_filter='--sc-filter-prescale' if a.sc_filter_prescale is not None else '--prescale-filter'
        sc_filter=False
    if sc_filter:
        kind,suffix,factor=('sc','sc',a.sc_filter_prescale) if a.sc_filter_prescale is not None else ('prescale','ps',a.prescale_filter)
        if factor<1:p.error('the prescale factor must be at least 1')
        if kind=='sc' and (not a.e88_param or not a.e88_param.is_file()):p.error('--sc-filter-prescale needs --e88-param <index>')
        if not a.sc_filter_exe.is_file():p.error(f'filter device not built: {a.sc_filter_exe}')
        stem=data_file.parent/f'run{a.run_number:06d}_{suffix}'
        for x in [stem.with_suffix('.dat'),Path(f'{stem}.marks.tsv'),Path(f'{stem}.summary.json')]:
            if x.exists():p.error(f'refusing existing filter output: {x}')
    if a.analyzer_http:
        try:parse_http(a.analyzer_http)
        except ValueError as exc:p.error(str(exc))
        exe=a.monitor_exe if a.no_file else a.analyzer_exe
        if not exe.is_file():p.error(f'online analyzer not built: {exe}')
        if a.e88_param and not a.e88_param.is_file():p.error(f'no parameter index: {a.e88_param}')
    a.output.mkdir(parents=True,exist_ok=bool(a.replay and not a.data_dir))
    env=dict(os.environ);env['LD_LIBRARY_PATH']=f'{a.prefix}/lib64:{a.prefix}/lib:'+env.get('LD_LIBRARY_PATH','')
    procs={};logs={};hbf_attempted=False;hbf_off_attempted=False;report={'mode':('replay' if a.replay else 'live')+('+webctl' if a.webctl else ''),'hardware_control':'Root HBF gate' if a.set_hbfstate else 'none','setup_manifest':str(a.manifest),'hbf_events':[],'run_number':a.run_number,'data_file':None if a.no_file else str(data_file),'no_file':a.no_file,'sources':{},'states':{},'commands':{}}
    if skipped_filter:report['side_filter_skipped']=f'{skipped_filter}: not run with --no-file'
    relays={}
    def launch(key,command,drop=None,preexec=None):
        logs[key]=open(a.output/f'{key}.log','w')
        if drop is None:
            procs[key]=subprocess.Popen([str(x) for x in command],stdout=logs[key],stderr=subprocess.STDOUT,env=env,cwd=ROOT,preexec_fn=preexec)
        else:
            procs[key]=subprocess.Popen([str(x) for x in command],stdout=subprocess.PIPE,stderr=subprocess.STDOUT,env=env,cwd=ROOT,preexec_fn=preexec)
            counts={'pattern':drop,'dropped':0}
            report.setdefault('log_lines_left_out',{})[key]=counts
            relays[key]=threading.Thread(target=relay_log,args=(procs[key].stdout,logs[key],drop,counts),daemon=True)
            relays[key].start()
        report['commands'][key]=[str(x) for x in command]
    def r(*args,db=0):
        return subprocess.check_output([str(a.prefix/'bin/redis-cli'),'-h','127.0.0.1','-p',str(port),'-n',str(db),*map(str,args)],text=True,stderr=subprocess.DEVNULL).strip()
    def state(key):
        text=(a.output/f'{key}.log').read_text(errors='replace')
        found=re.findall(r'\] ([A-Z ]+)---> ([A-Z ]+)',text)
        return found[-1][1].strip() if found else ''
    def drive(command,target,keys=None):
        deadline=time.monotonic()+35;keys=[k for k in (keys or devices) if k not in optional]
        while time.monotonic()<deadline:
            for key in devices:
                if key not in optional and procs[key].poll() is not None:raise RuntimeError(f'{key} exited ({procs[key].returncode}) before {target}')
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
    started=time.time()
    follower=None;analyzer_logs=[];monitor=False
    if a.analyzer_http and a.no_file:
        # Best effort on the dqm output; skipped (and recorded) when the port is taken, the DAQ starts anyway.
        monitor=port_free(a.analyzer_http)
        report['online_analyzer']={'http':a.analyzer_http,'input':'dqm','executable':str(a.monitor_exe.resolve()),'e88_param':str(a.e88_param.resolve()) if a.e88_param else None}
        if not monitor:report['online_analyzer']['error']='analyzer port in use; not started'
    elif a.analyzer_http:
        fems=[str(setup['data_sources'][role]['endpoint']) for role in roles.values()]
        param=a.e88_param.resolve() if a.e88_param else None
        def spawn(command,log_path):
            log=open(log_path,'a');analyzer_logs.append(log)
            # Lower priority than the DAQ processes; the analyzer may fall behind, the DAQ must not.
            return subprocess.Popen(command,stdout=log,stderr=subprocess.STDOUT,env=env,cwd=ROOT,preexec_fn=analyzer_limits(a.analyzer_memory_gb))
        follower=RunFileFollower(data_file.parent,started,
            lambda f,use_param:analyzer_command(a.analyzer_exe.resolve(),f,a.analyzer_http,param if use_param else None,fems),
            spawn,lambda:port_free(a.analyzer_http),a.output,with_param=param is not None)
        report['online_analyzer']={'http':a.analyzer_http,'executable':str(a.analyzer_exe.resolve()),'e88_param':str(param) if param else None,'runs':follower.records}
    with socket.socket() as sock:sock.bind(('127.0.0.1',0));port=sock.getsockname()[1]
    redisconf=a.output/'redis.conf'
    redisconf.write_text(f'bind 127.0.0.1\nport {port}\nprotected-mode yes\nsave ""\nappendonly no\ndir {a.output}\n')
    tag=str(os.getpid());inputs=[svc.replace('STF','Input') for svc in roles]
    devices=[*([] if a.no_file else ['FileSink']),*(['FilterSink','ScFilter'] if sc_filter else []),*(['OnlineMonitor'] if monitor else []),'TimeFrameBuilder',*roles]
    optional={'OnlineMonitor'}  # may fail without stopping the DAQ
    if not a.replay:devices+=inputs
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
        # One pull socket with one distinct peer per STF source; no source cross-talk.
        endpoints=[('TimeFrameBuilder','out','push','bind'),('TimeFrameBuilder','in','pull','bind')]
        links=[]
        if not a.no_file:
            endpoints.append(('FileSink','in','pull','connect'));links.append(('TimeFrameBuilder','out','FileSink','in'))
        if monitor:
            endpoints+=[('TimeFrameBuilder','dqm','push','bind'),('OnlineMonitor','in','pull','connect')]
            links.append(('TimeFrameBuilder','dqm','OnlineMonitor','in'))
        if sc_filter:
            # Side output: TimeFrameBuilder dqm -> ScFilter -> FilterSink (run<NNNNNN>_sc.dat or _ps.dat).
            endpoints+=[('TimeFrameBuilder','dqm','push','bind'),('ScFilter','in','pull','connect'),('ScFilter','out','push','bind'),('FilterSink','in','pull','connect')]
            links+=[('TimeFrameBuilder','dqm','ScFilter','in'),('ScFilter','out','FilterSink','in')]
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
        if not a.no_file:launch('FileSink',command('FileSink',a.prefix/'bin/FileSink',['--in-chan-name','in','--prefix',data_file.parent,'--run-number-format','run{:06d}','--run_number',str(a.run_number),'--ext','.dat','--openmode','create','--discard','false','--multipart','false']))
        if sc_filter:
            launch('FilterSink',command('FilterSink',a.prefix/'bin/FileSink',['--in-chan-name','in','--prefix',data_file.parent,'--run-number-format','run{:06d}_'+suffix,'--run_number',str(a.run_number),'--ext','.dat','--openmode','create','--discard','false','--multipart','false']))
            settings_file=a.output/'run-settings.txt'
            run_settings.write(settings_file,kind,factor)
            args=['--in-chan-name','in','--out-chan-name','out','--run_number',str(a.run_number),'--marks-dir',data_file.parent,'--kind',kind,'--prescale',str(factor),
                  '--run-settings',settings_file,'--suffix',suffix]
            # The parameters are given to the plain prescale too when known, so that a run can switch to the SC filter.
            if a.e88_param:args+=['--e88-param',a.e88_param.resolve()]
            launch('ScFilter',command('ScFilter',a.sc_filter_exe.resolve(),args))
            report['side_filter']={'kind':kind,'prescale':factor,'e88_param':str(a.e88_param.resolve()) if a.e88_param else None,'executable':str(a.sc_filter_exe.resolve()),'run_settings':str(settings_file)}
        if monitor:
            margs=['--in-chan-name','in','--http',f'http:{a.analyzer_http}','--monitor-run-number',str(a.run_number),'--expect-fem',','.join(str(setup['data_sources'][role]['endpoint']) for role in roles.values())]
            if a.e88_param:margs+=['--e88-param',a.e88_param.resolve()]
            launch('OnlineMonitor',command('OnlineMonitor',a.monitor_exe.resolve(),margs),preexec=analyzer_limits(a.analyzer_memory_gb))
        for group in ['TimeFrameBuilder',f'TimeFrameBuilder-{tag}']:r('HSET','parameters:'+group,'metrics-uri',f'tcp://127.0.0.1:{port}/0',db=2)
        launch('TimeFrameBuilder',command('TimeFrameBuilder',a.prefix/'bin/TimeFrameBuilder',['--in-chan-name','in','--out-chan-name','out','--buffer-timeout','2000','--buffer-depth-limit','10000','--output-incomplete-tf','true','--discard-output','true' if a.no_file else 'false']),drop=(DISCARD_LINE,*FLOOD_LINES) if a.no_file else FLOOD_LINES)
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
        if a.webctl:
            def keep_presence():
                # Devices started with --id do not publish their presence key; webctl lists instances and
                # connecting devices find their peers through it, so the launcher keeps it alive.
                for svc in devices:r('SETEX',f'daq_service:{svc}:{svc}-{tag}:presence',120,'owned-launcher-placeholder')
            def current_run():
                try:return int(r('GET','run_info:run_number'))
                except (subprocess.CalledProcessError,ValueError):return None
            analyzer_poll=(lambda:follower.poll(current_run())) if follower else (lambda:None)
            run_webctl(a,launch,procs,logs,env,devices,inputs,setup,port,report,keep_presence,analyzer_poll,optional)
            return
        for cmd,target in [('INIT DEVICE','INITIALIZED'),('BIND','BOUND'),('CONNECT','DEVICE READY'),('INIT TASK','READY')]:drive(cmd,target)
        tftext=(a.output/'TimeFrameBuilder.log').read_text(errors='replace')
        if not re.search(rf'number of source = {len(roles)}\b',tftext):raise RuntimeError(f'TimeFrameBuilder did not bind exactly {len(roles)} input peers')
        drive('RUN','RUNNING',keys=[*([] if a.no_file else ['FileSink']),'TimeFrameBuilder',*(['FilterSink','ScFilter'] if sc_filter else [])] if a.replay else devices)
        if not a.replay:
            wait_sampler_tcp(procs, a.output, inputs)
        print(f'RUNNING: {len(roles)} STF peers -> TimeFrameBuilder -> '+('(no file)' if a.no_file else 'FileSink'),flush=True)
        if a.set_hbfstate:
            hbf_attempted=True;hbf('on')
        deadline=time.monotonic()+a.seconds
        while time.monotonic()<deadline:
            for key in devices:
                if procs[key].poll() is not None:
                    if key in optional:report.setdefault('optional_exits',{})[key]=procs[key].returncode;continue
                    raise RuntimeError(f'{key} exited during capture')
            if follower:follower.poll(a.run_number)
            time.sleep(.2)
        if a.set_hbfstate:
            hbf_off_attempted=True;hbf('off');time.sleep(1)
        report['states']={key:state(key) for key in devices}
        r('PUBLISH','daqctl',json.dumps({'command':'change_state','value':'STOP','services':['all'],'instances':['all']}));time.sleep(2)
        r('PUBLISH','daqctl',json.dumps({'command':'change_state','value':'END','services':['all'],'instances':['all']}))
        for key in reversed(devices):
            try:procs[key].wait(timeout=10)
            except subprocess.TimeoutExpired:
                if key in optional:continue
                raise RuntimeError(f'{key} did not end cleanly')
        report['exit_codes']={k:procs[k].returncode for k in devices}
        if any(v for k,v in report['exit_codes'].items() if k not in optional):raise RuntimeError('nonzero device exit')
        report['status']='CAPTURE_COMPLETE'
    except Exception as exc:
        report['status']='FAILED';report['error']=str(exc);raise
    finally:
        # Always try to switch HBF off after any on attempt, even a failed one.
        # In webctl mode the hook may have switched it on at any RUN: switch it off unconditionally.
        if a.webctl and a.set_hbfstate:
            try:hbf('off')
            except Exception as exc:report['hbf_restore_error']=str(exc)
        elif hbf_attempted and not hbf_off_attempted:
            try:hbf_off_attempted=True;hbf('off')
            except Exception as exc:report['hbf_restore_error']=str(exc)
        if follower:
            try:follower.close()
            except Exception as exc:report['online_analyzer']['close_error']=str(exc)
            for log in analyzer_logs:log.close()
        # Only Popen objects created by this process; no process-name signalling.
        for key,proc in reversed(list(procs.items())):
            if proc.poll() is None:
                proc.terminate()
                try:proc.wait(timeout=5)
                except subprocess.TimeoutExpired:proc.kill();proc.wait()
        for relay in relays.values():relay.join(timeout=10)
        for log in logs.values():log.close()
        report['final_exit_codes']={k:p.returncode for k,p in procs.items()}
        if sc_filter:
            # Summaries written by the filter at every STOP of this session (one per run number).
            summaries={}
            for f in sorted(data_file.parent.glob(f'run*_{suffix}.summary.json')):
                if f.stat().st_mtime>=started:
                    try:summaries[f.name]=json.loads(f.read_text())
                    except ValueError as exc:summaries[f.name]={'unreadable':str(exc)}
            report['side_filter_summaries']=summaries
        (a.output/'report.json').write_text(json.dumps(report,indent=2)+'\n')
def run_webctl(a,launch,procs,logs,env,devices,inputs,setup,port,report,keep_presence,analyzer_poll=lambda:None,optional=()):
    """Start daq-webctl with the HBF hooks and wait until the devices end or a signal arrives."""
    hook=[sys.executable,str(ROOT/'nestdaq/scripts/webctl-hook.py')]
    opts=['--output',str(a.output),'--redis-port',str(port),'--inputs',*([] if a.replay else inputs)]
    if a.set_hbfstate:opts+=['--set-hbfstate',str(a.set_hbfstate.resolve()),'--root',setup['timing_root']['endpoint']]
    hooks={f'--{e}':shlex.join(hook+[e]+opts) for e in ['pre-run','post-run','pre-stop','post-stop']}
    command=[a.prefix/'bin/daq-webctl','--http-uri',f'http://{a.webctl}','--redis-uri',f'tcp://127.0.0.1:{port}/0']
    # The controller page of this repository (nestdaq/webctl): state colours, whether data is saved, filter settings.
    if (ROOT/'nestdaq/webctl/daq-webctl.html').is_file():command+=['--doc-root',ROOT/'nestdaq/webctl']
    for k,v in hooks.items():command+=[k,v]
    launch('webctl',command)
    # daq-webctl terminates when a browser drops its WebSocket abruptly (unhandled "Broken pipe").
    # It keeps no state of its own (the devices and Redis do), so it is restarted, a bounded number of times.
    restarts=[]
    def stop(signum,frame):raise KeyboardInterrupt(f'signal {signum}')
    signal.signal(signal.SIGTERM,stop)
    print(f'WEBCTL: http://{a.webctl}/daq-webctl.html  ({len(inputs)} sources; END in the browser or Ctrl-C ends the session)',flush=True)
    try:
        last=0
        # The session ends when the DAQ devices have exited; an optional device left over is stopped by the caller.
        while any(procs[k].poll() is None for k in devices if k not in optional):
            if procs['webctl'].poll() is not None:
                restarts.append({'time':time.time(),'returncode':procs['webctl'].returncode})
                report['webctl_restarts']=restarts
                if len(restarts)>20:raise RuntimeError('daq-webctl exited too often')
                logs['webctl'].close()
                logs['webctl']=open(a.output/'webctl.log','a')
                procs['webctl']=subprocess.Popen([str(x) for x in command],stdout=logs['webctl'],stderr=subprocess.STDOUT,env=env,cwd=ROOT)
                print(f'WEBCTL: restarted after exit code {restarts[-1]["returncode"]}',flush=True)
            if time.monotonic()-last>30:keep_presence();last=time.monotonic()
            try:analyzer_poll()
            except Exception as exc:report.setdefault('online_analyzer_errors',[]).append(str(exc))
            time.sleep(.5)
        report['status']='WEBCTL_SESSION_ENDED'
    except KeyboardInterrupt as exc:
        report['status']='WEBCTL_SESSION_INTERRUPTED';report['interrupt']=str(exc)
    report['exit_codes']={k:procs[k].returncode for k in devices}
    hooks_file=a.output/'hooks.jsonl'
    report['hooks']=[json.loads(x) for x in hooks_file.read_text().splitlines()] if hooks_file.exists() else []
    if any(h.get('status')!='OK' for h in report['hooks']):report['hook_failures']=True


if __name__=='__main__':main()
