"""Execute native channel ownership transitions from the camera-observed OFF.

Native setters and bank reads execute. Visibility messages are captured and
their original consumer is invoked explicitly, not by a real task scheduler.
"""
import argparse
import hashlib
import json
import struct
import sys
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT/'src'),str(ROOT/'tools')]


def main():
    p=argparse.ArgumentParser(description=__doc__)
    for key in ('source','image','report','deps','output'):
        p.add_argument('--'+key,type=Path,required=True)
    a=p.parse_args();sys.path.insert(0,str(a.deps.resolve()))
    from z30_extensions.container import extract_eg
    from verify_sound_settings import Fixture
    from read_aspect_trace import decode
    from unicorn import UC_HOOK_CODE,UC_HOOK_MEM_INVALID,UC_HOOK_MEM_WRITE
    from unicorn.arm_const import UC_ARM_REG_R0,UC_ARM_REG_R1,UC_ARM_REG_R2,UC_ARM_REG_R4,UC_ARM_REG_R5
    stock=extract_eg(a.source.read_bytes());image=a.image.read_bytes()
    report=json.loads(a.report.read_text());feature=report['features']['aspect_profiles']
    f={k:int(v,16) for k,v in feature['functions'].items()}
    props={0x501:2,0x1129:0};s=Fixture(stock,image,report,{},properties=props)
    state=s.state+feature['state_offset'];metadata=feature['independent_border']
    cache=state+metadata['cache_state_word_offset'];lease=state+metadata['lease_state_word_offset']
    _,bss,length,_=struct.unpack_from('<4I',stock,0x140b0)
    def invalid(u,access,address,size,value,data):
        if bss<=address and address+size<=bss+length:s.map(address,size);return True
        return False
    s.u.hook_add(UC_HOOK_MEM_INVALID,invalid)
    banks={mask:s.call(0x104b5e30,(3,mask,0)) for mask in (1,2,4)}
    for bank in banks.values():s.map(bank,0x400)
    bank=banks[1];busy=[0];ready=[1];flags=[1];requests=[];visibility=[]
    def frontier(u,address,length,data):
        if address==0x104b9348:
            ptr=u.reg_read(UC_ARM_REG_R0);u.mem_write(ptr,bytes(0x468))
            u.mem_write(ptr,struct.pack('<I',flags[0]));u.mem_write(ptr+8,struct.pack('<4h',0,36,720,442));s.ret(0)
        elif address==0x10453fac:
            requests.append([u.reg_read(UC_ARM_REG_R1),u.reg_read(UC_ARM_REG_R2)]);s.ret(0)
        elif address==0x103c5502:
            visibility.append([u.reg_read(reg) for reg in (UC_ARM_REG_R0,UC_ARM_REG_R1,UC_ARM_REG_R2)]);s.ret(1)
        elif address==0x103c581c:s.ret(7)
        elif address==0x104be4a0:s.ret(ready[0])
        elif address==0x1044fcf6:s.ret(busy[0])
        else:s.ret(0)
    for address in (0x104b9348,0x10453fac,0x103c5502,0x103c581c,0x104be4a0,0x1044fcf6,0x1044dd08):
        s.u.hook_add(UC_HOOK_CODE,frontier,begin=address,end=address)
    def lock(u,address,length,data):s.ret(1)
    for address in (0x10199bb0,0x10199baa,0x1021cd56,0x1021cd38,0x1021ce38,0x1021ce24):
        s.u.hook_add(UC_HOOK_CODE,lock,begin=address,end=address)
    s.map(0xc237c380,256)
    s.map(0xc1d611c8,0x500)
    for offset in (1,3,0x4f9):s.u.mem_write(0xc1d611c8+offset,b'\x03')
    device_state,handle=0x50088000,0x50085000
    s.u.mem_write(device_state,bytes(0x2000));s.u.mem_write(device_state+0x234,struct.pack('<I',1))
    s.u.mem_write(0xc1808170,b'\x00')
    s.u.mem_write(0xc1808170+0x34,struct.pack('<I',device_state))
    s.u.mem_write(0xc1808170+0x1c,struct.pack('<I',handle))
    s.u.mem_write(handle,struct.pack('<II',0x143,0))
    s.map(0xc1d57ac0+0x2f00,8);s.u.mem_write(0xc1d57ac0+0x2f00,struct.pack('<II',8192,8192))
    s.map(0x20141000,4096);writes=[]
    def device(u,access,address,size,value,data):writes.append([hex(address),value])
    s.u.hook_add(UC_HOOK_MEM_WRITE,device,begin=0x20141000,end=0x20141fff)
    def word(addr):return int.from_bytes(s.u.mem_read(addr,4),'little')
    def enabled():return word(bank+0x14)
    def consume_visibility(desired):
        obj,row=0x5008b000,0x5008b100
        s.u.mem_write(obj,struct.pack('<III',1,row,0));s.u.mem_write(row,struct.pack('<II',3,desired))
        assert s.call(0x10364788,(0,obj))==1
        assert s.u.mem_read(device_state+0x20f,1)==bytes([desired])
    def reset(profile=1,channel=0,hidden_af=False):
        for b in banks.values():s.u.mem_write(b,bytes(0x400))
        s.u.mem_write(bank+0x14,struct.pack('<I',channel))
        s.u.mem_write(state,struct.pack('<IIII',profile,0,0,0));s.u.mem_write(cache,bytes(12+64+4))
        s.u.mem_write(0xc237c3d2,struct.pack('<4H',0,292,5568,3420))
        if hidden_af:s.u.mem_write(bank+0x2c,struct.pack('<II6H',0,1,100,100,180,180,4,9))
        props[0x501]=2;busy[0]=0;ready[0]=1;flags[0]=1;requests.clear();visibility.clear()
    def publish():s.call(f['aspect_border_publish'],(0,))
    cases=[]
    for grid in (0,1):
        reset();props[0x1129]=grid;publish()
        assert enabled()==1 and word(lease)==1 and visibility==[[1,3,1]] and requests==[[8,1]]
        publish();assert len(visibility)==len(requests)==1
        consume_visibility(1)
        payload=0x50080004;s.u.mem_write(payload-4,b'\x03\0\0\0'+bytes(0x4b4))
        s.call(0x103c0568,(1,3,payload))
        assert s.call(0x1036471e,(0,payload-4))==1
        assert writes and word(state+12)==1
        s.call(f['aspect_trace_view_menu']);parsed=decode(bytes(s.u.mem_read(state+16,15384)))
        sample=next(r for r in parsed['records'] if r['event']==5)
        assert sample['border']['rectangle_channel_owned'] and sample['border']['stock_grid_enabled']==grid
        s.u.mem_write(state,bytes(4));publish()
        assert enabled()==0 and word(lease)==0 and visibility[-1]==[1,3,0]
        consume_visibility(0)
        # Original native frame-begin rectangle-disable window. It clears the
        # hardware bitmap before any new visible component is composed.
        s.u.mem_write(device_state+0x20e,b'\x01')
        s.u.reg_write(UC_ARM_REG_R4,0xc1808170);s.u.reg_write(UC_ARM_REG_R5,0)
        s.block(0x10282462,0x10282482)
        assert s.u.mem_read(device_state+0x20e,2)==b'\0\0'
        cases.append(dict(grid=grid,disabled_empty_channel_acquired=True,native_visibility_consumer=True,
                          native_rectangle_consumer=True,off_restored_disabled=True))
    reset(channel=1,hidden_af=True);publish();assert word(lease)==0 and not visibility
    s.u.mem_write(state+12,struct.pack('<I',1));s.u.mem_write(state,bytes(4));publish()
    assert enabled()==1 and not visibility and requests[-1]==[8,1]
    cases.append(dict(native_enabled_channel_never_owned=True))
    reset(hidden_af=True);publish();assert enabled()==0 and word(lease)==0 and not requests and not visibility
    cases.append(dict(hidden_native_rectangles_not_revealed=True))
    for batch in (False,True):
        reset();publish();assert word(lease)==1
        if batch:
            data=0x5008c000;s.u.mem_write(data,struct.pack('<II',3,1));s.call(0x104b6bdc,(3,data,1))
        else:s.call(0x104b6b0c,(1,3,1))
        assert word(lease)==0
        s.u.mem_write(state+12,struct.pack('<I',1));s.u.mem_write(state,bytes(4));before=len(visibility);publish()
        assert enabled()==1 and len(visibility)==before
        cases.append(dict(native_owner_takes_over=True,batch=batch))
    reset();publish();s.u.mem_write(bank+0x30,struct.pack('<I',1))
    s.call(f['aspect_border_lease_release']);assert word(lease)==0 and enabled()==1
    cases.append(dict(active_native_records_never_hidden_on_release=True))
    reset();publish();s.u.mem_write(state+12,struct.pack('<I',1))
    s.call(0x104b6b0c,(1,3,0));assert word(lease)==0 and enabled()==0
    s.u.mem_write(state,bytes(4));before=len(requests);publish()
    assert word(state+12)==0 and len(requests)==before
    cases.append(dict(external_hide_then_off_clears_stale_dirty_state=True))
    for cause in ('busy','not_ready','snapshot','source'):
        reset();publish();assert word(lease)==1
        if cause=='busy':busy[0]=1
        elif cause=='not_ready':ready[0]=0
        elif cause=='snapshot':flags[0]=0
        else:s.u.mem_write(0xc237c3d2,bytes(8))
        publish();assert word(lease)==0 and enabled()==0
        cases.append(dict(release_on=cause))
    reset(profile=0);publish();assert not requests and not visibility
    result=dict(image_sha256=hashlib.sha256(image).hexdigest(),cases=cases,
                native_register_writes=len(writes),native_bank_and_setters_execute=True,
                native_visibility_consumer_executes=True,actual_camera_pixels_verified=False,
                models=['Readiness, viewport, locks and initialized display registration are fixtures.',
                        'Visibility publication is intercepted; original consumer is invoked explicitly in order.',
                        'Native frame-begin disable slice executes; no physical worker timing verified.'])
    a.output.write_text(json.dumps(result,indent=2)+'\n')
    print('PASS',len(cases),'ownership/lifecycle cases, native setters and visibility/rectangle consumers')


if __name__=='__main__':main()
