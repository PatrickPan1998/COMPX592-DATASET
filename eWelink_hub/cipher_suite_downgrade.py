import sys, socket, struct, os, time

def ext_sni(sni_name: bytes):
    # server_name_list: list_length(2) + (name_type(1) + name_length(2) + name)
    server_name = b'\x00' + struct.pack('!H', len(sni_name)) + sni_name
    server_name_list = struct.pack('!H', len(server_name)) + server_name
    ext_type = struct.pack('!H', 0x0000)
    ext_len = struct.pack('!H', len(server_name_list))
    return ext_type + ext_len + server_name_list

def ext_ems():
    # extended_master_secret (type 0x0017, no data)
    return struct.pack('!HH', 0x0017, 0)

def ext_renego():
    # renegotiation_info (type 0xff01), data = renegotiated_connection length (1) = 0
    ext_type = struct.pack('!H', 0xff01)
    ext_data = b'\x00'  
    return ext_type + struct.pack('!H', len(ext_data)) + ext_data

def ext_alpn(proto_name: bytes):
    # ALPN (type 0x0010): extension_data = protocol_name_list (2 bytes len) + [name_len(1)+name]
    proto = bytes([len(proto_name)]) + proto_name
    proto_list = struct.pack('!H', len(proto)) + proto
    ext_type = struct.pack('!H', 0x0010)
    return ext_type + struct.pack('!H', len(proto_list)) + proto_list

# ---- Build ClientHello ----
def build_clienthello(cipher_list, sni_hostname=None, add_alpn=False):
    # TLS Record Header (Handshake, TLS1.2)
    record_hdr = b'\x16\x03\x03'  # type=22(handshake), version=3.3 (TLS1.2)

    client_version = b'\x03\x03' #TLS1.2
    random_bytes = os.urandom(32) #a 32 bit random value (client_random
    session_id = b'\x00'  # 0
    # cipher suites: 2-byte length + each suite 2 bytes
    cs_bytes = b''.join(struct.pack('!H', c) for c in cipher_list)
    cs_field = struct.pack('!H', len(cs_bytes)) + cs_bytes
    compression = b'\x01\x00'  # null

    ch_body = client_version + random_bytes + session_id + cs_field + compression

    # extensions
    exts = b''
    if sni_hostname:
        exts += ext_sni(sni_hostname.encode())
    # Add extended master secret
    exts += ext_ems()
    # Add renegotiation_info
    exts += ext_renego()
    # Optionally ALPN (mqtt) for MQTT over TLS (port 8883 common)
    if add_alpn:
        exts += ext_alpn(b"mqtt")

    if exts:
        ch_body += struct.pack('!H', len(exts)) + exts

    # Handshake header: type(1) + length(3)
    hs_hdr = b'\x01'+ struct.pack('!I', len(ch_body))[1:]
    handshake = hs_hdr + ch_body
    record_len = struct.pack('!H', len(handshake))
    record = record_hdr + record_len + handshake
    #print(record)
    return record

# ---- Parse ServerHello for chosen cipher ----
def parse_serverhello_cipher(data: bytes):
    # Scan for handshake type 0x02 and parse minimal ServerHello to extract cipher (2 bytes)
    try:
        i = 0
        while True:
            idx = data.find(b'\x02', i) #0x02 serverhello
            if idx == -1:
                return None
            if idx + 4 > len(data):
                return None
            hs_len = struct.unpack('!I', b'\x00' + data[idx+1:idx+4])[0]
            start = idx + 4 #serverhello body start position            # need at least version(2) + random(32) + sess_id_len(1) + cipher(2) + comp(1)
            if start + 38 <= len(data):
                sid_len = data[start + 34]
                cipher_offset = start + 35 + sid_len
                if cipher_offset + 2 <= len(data):
                    #cipher suite
                    cipher_bytes = data[cipher_offset:cipher_offset+2]
                    return struct.unpack('!H', cipher_bytes)[0]
                else:
                    return None
            i = idx + 1
    except Exception:
        return None

# ---- Receive helper ----
def recv_all(sock, timeout=1.0):
    sock.settimeout(timeout)
    total = b''
    try:
        while True:
            chunk = sock.recv(8192)
            if not chunk:
                break
            total += chunk
            # continue until timeout
    except socket.timeout:
        pass
    except Exception:
        pass
    return total #all data

# ---- Suite presets ----
PRESETS = {
    'ccm8': [0xC0A8, 0x00FF],          # CCM_8 + SCSV
    'ccm':  [0xC0A4, 0x00FF],          # AES_128_CCM + SCSV
    'cbc':  [0x00AE, 0x00FF],          # AES_128_CBC_SHA256 + SCSV
    'rsa':  [0x002f, 0x009c],
    'multi': [0xC0A4, 0x00AE, 0xC0A8, 0x00FF]  # CCM, CBC, CCM_8, SCSV
}

def human_name_for_cipher(val):
    mapping = {
        0xC0A8: "TLS_PSK_WITH_AES_128_CCM_8 (0xc0a8)",
        0xC0A4: "TLS_PSK_WITH_AES_128_CCM (0xc0a4)",
        0xC0A5: "TLS_PSK_WITH_AES_256_CCM (0xc0a5)",
        0x00AE: "TLS_PSK_WITH_AES_128_CBC_SHA256 (0x00ae)",
        0x00FF: "TLS_EMPTY_RENEGOTIATION_INFO_SCSV (0x00ff)",
	#RSA
	0x002f: "TLS_RSA_WITH_AES_128_CBC_SHA (0x002f)",
        0x009c: "TLS_RSA_WITH_AES_128_CCM (0x009c)",
    }
    return mapping.get(val, f"0x{val:04x}")

# ---- Main CLI ----
def main():
    if len(sys.argv) < 3:
        print("Usage: python3 probe_clienthello_full.py <host> <port> [sni_hostname] [suite]")
        print("suite: ccm8 | ccm | cbc | multi (default multi)")
        sys.exit(1)
    host = sys.argv[1]
    port = int(sys.argv[2])
    sni = sys.argv[3] if len(sys.argv) >= 4 else host
    suite = sys.argv[4] if len(sys.argv) >= 5 else 'multi'
    if suite not in PRESETS:
        print("Unknown suite preset")
        sys.exit(1)

    cipher_list = PRESETS[suite]
    add_alpn = (port == 8883)

    print(f"- Target: {host}:{port}  SNI={sni}  preset={suite}  cipher_list={[hex(x) for x in cipher_list]}  ALPN={add_alpn}")
    pkt = build_clienthello(cipher_list, sni_hostname=sni, add_alpn=add_alpn)

    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM) #create a network connection object
    s.settimeout(6)
    try:
        print("- Connecting...")
        s.connect((host, port))
    except Exception as e:
        print("Connect error:", e)
        return
    try:
        s.sendall(pkt)
    except Exception as e:
        print("Send error:", e)
        s.close()
        return

    data = recv_all(s, timeout=1.5)
    if not data:
        print("- No response or timeout from server.")
        s.close()
        return

    print(f"- Received {len(data)} bytes (hex preview):")
    print(data[:200].hex(), "..." if len(data) > 200 else "")

    if len(data) >= 7 and data[0] == 0x15: # 0x15: TLS alert
        try:
            rec_len = struct.unpack('!H', data[3:5])[0]
            if rec_len >= 2:
                level = data[5]
                desc = data[6]
                print(f"! Server returned TLS Alert: level={level} desc={desc}")
                #40 = handshake_failure
								#42 = bad_certificate
								#70 = protocol_version
								#71 = insufficient_security
								#80 = internal_error
                if desc == 71:
                    print("! Description 71 = insufficient_security (server considered our params insufficient).")
        except Exception:
            pass

    cipher = parse_serverhello_cipher(data)
    if cipher:
        print(f"- ServerHello chosen cipher: {human_name_for_cipher(cipher)} (0x{cipher:04x})")
    else:
        print("- Could not parse ServerHello chosen cipher. The server may have returned an Alert or partial records.")
    s.close()

if __name__ == '__main__':
    main()
    
    
    
