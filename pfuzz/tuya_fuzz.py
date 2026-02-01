import tinytuya
import time

print(1)
tinytuya.set_debug(True)
# 设备信息
d = tinytuya.Device(
    dev_id="eb6ed3524b01e430e7bbuo",
    address="192.168.0.106",     # 插座的局域网 IP
    local_key="0.I]^6liWd3=eF.G",
    version=3.4
)
# print(d.status())
# print(d.detect_available_dps())
# res=d.
# print(res)
'''
payload可以fuzzing的地方
'''

payload=tinytuya.MessagePayload(
    cmd=16, 
    payload=b'{}'
)
# res=d.set_value(9,10)
print("payload: ", payload)
resp = d._send_receive(payload)
print(resp)


