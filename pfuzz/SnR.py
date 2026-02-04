import socket
import tinytuya

# ============================================
#  Edit distance & similarity
# ============================================

def EditDistanceRecursive(str1, str2):
    """计算两个字符串的编辑距离"""
    edit = [[i + j for j in range(len(str2) + 1)] for i in range(len(str1) + 1)]
    for i in range(1, len(str1) + 1):
        for j in range(1, len(str2) + 1):
            d = 0 if str1[i - 1] == str2[j - 1] else 1
            edit[i][j] = min(
                edit[i - 1][j] + 1,       # 删除
                edit[i][j - 1] + 1,       # 插入
                edit[i - 1][j - 1] + d    # 替换
            )
    return edit[len(str1)][len(str2)]


def SimilarityScore(str1, str2):
    """
    两个字符串的相似度（0~100），防止除 0。
    """
    s1 = (str1 or "").strip()
    s2 = (str2 or "").strip()

    len1, len2 = len(s1), len(s2)
    if len1 == 0 and len2 == 0:
        return 100.0

    max_len = max(len1, len2)
    if max_len == 0:
        return 0.0

    ED = EditDistanceRecursive(s1, s2)
    sim = 1.0 - ED / max_len
    sim = max(0.0, min(sim * 100.0, 100.0))
    return round(sim, 2)


# ============================================
#  Messenger：负责真正发包
# ============================================

class Messenger:
    # 共享一个 TinyTuya 设备，避免频繁重连
    shared_tuya_device = None
    shared_tuya_fingerprint = None  # (dev_id, address, local_key, version)

    def __init__(self, restoreSeed):
        """
        restoreSeed 是 Snipuzz 传进来的“恢复报文 seed”（Seed 对象）
        """
        self.restoreSeed = restoreSeed
        self.restore = restoreSeed   # DryRun / Probe 会用到

        # TinyTuya 相关配置
        self.tuya_dev_id = None
        self.tuya_address = None
        self.tuya_local_key = None
        self.tuya_version = 3.4
        self.tuya_device = None

        # ⭐ 默认 cmd（可以被种子里的 Cmd 覆盖）
        self.default_cmd = 13

        # 如果 restoreSeed 里有 DevID / LocalKey，则初始化 TinyTuya
        if restoreSeed and getattr(restoreSeed, "M", None) and restoreSeed.M:
            cfg = restoreSeed.M[0].raw
            if "DevID" in cfg and "LocalKey" in cfg:
                self.tuya_dev_id = str(cfg["DevID"]).strip()
                self.tuya_address = str(cfg.get("Address", cfg.get("IP", ""))).strip()
                self.tuya_local_key = str(cfg["LocalKey"]).strip()
                ver_str = str(cfg.get("Version", "3.4")).strip()
                try:
                    self.tuya_version = float(ver_str)
                except ValueError:
                    self.tuya_version = 3.4

                if "Cmd" in cfg:
                    try:
                        self.default_cmd = int(str(cfg["Cmd"]).strip(), 0)
                    except ValueError:
                        self.default_cmd = 13

                self._init_tuya_device()

    def _tuya_fingerprint(self):
        return (self.tuya_dev_id, self.tuya_address, self.tuya_local_key, self.tuya_version)

    def _invalidate_shared_tuya(self):
        Messenger.shared_tuya_device = None
        Messenger.shared_tuya_fingerprint = None
        self.tuya_device = None

    def _init_tuya_device(self):
        """根据当前 tuya_* 配置初始化 TinyTuya Device（只真正 init 一次）"""
        if not (self.tuya_dev_id and self.tuya_address and self.tuya_local_key):
            print("TinyTuya config incomplete, Tuya mode disabled.")
            self.tuya_device = None
            return

        fp = self._tuya_fingerprint()

        if Messenger.shared_tuya_device is not None and Messenger.shared_tuya_fingerprint == fp:
            self.tuya_device = Messenger.shared_tuya_device
            return

        print("[Messenger] Init TinyTuya device:", self.tuya_dev_id, self.tuya_address)
        device = tinytuya.Device(
            dev_id=self.tuya_dev_id,
            address=self.tuya_address,
            local_key=self.tuya_local_key,
            version=self.tuya_version
        )
        Messenger.shared_tuya_device = device
        Messenger.shared_tuya_fingerprint = fp
        self.tuya_device = device

    # ---------------------------------------------------------
    #  Snipuzz 调用：DryRun 阶段
    # ---------------------------------------------------------
    def DryRunSend(self, squence):
        """
        DryRun：先把当前 seed 的所有 message 发一遍，
        再把 restoreSeed 的所有 message 发一遍，确认环境 OK。
        返回：成功 -> squence；失败 -> "#error"/"#crash"
        """
        for message in squence.M:
            response = self.sendMessage(message)
            if response in ("#error", "#crash"):
                return response
            squence.R.append(response)

        if self.restore and getattr(self.restore, "M", None):
            for message in self.restore.M:
                response = self.sendMessage(message)
                if response in ("#error", "#crash"):
                    return response

        return squence

    # ---------------------------------------------------------
    #  Snipuzz 调用：Probe 阶段
    # ---------------------------------------------------------
    def ProbeSend(self, squence, index):
        """
        Probe 阶段发送一个 seed 的完整序列，返回第 index 条消息的响应。
        如遇 #error / #crash，直接返回相应标记字符串。
        """
        res = ""
        for i in range(len(squence.M)):
            response = self.sendMessage(squence.M[i])
            if response in ("#error", "#crash"):
                return response
            if i == index:
                res = response

        if self.restore and getattr(self.restore, "M", None):
            for i in range(len(self.restore.M)):
                restoreResponse = self.sendMessage(self.restore.M[i])
                if restoreResponse in ("#error", "#crash"):
                    return restoreResponse

        return res

    # ---------------------------------------------------------
    #  Snipuzz 调用：SnippetMutate 阶段
    # ---------------------------------------------------------
    def SnippetMutationSend(self, squence, index):
        """
        SnippetMutate 阶段发送序列，并根据响应与 PR/PS 判断是否 #interesting
        """
        res = ""
        for i in range(len(squence.M)):
            response = self.sendMessage(squence.M[i])
            if response in ("#error", "#crash"):
                return response
            if i == index:
                res = response

        if self.restore and getattr(self.restore, "M", None):
            for i in range(len(self.restore.M)):
                restoreResponse = self.sendMessage(self.restore.M[i])
                if restoreResponse in ("#error", "#crash"):
                    return restoreResponse

        # ✅ 方案A：空响应直接忽略，不算 interesting
        if (res or "").strip() == "":
            return ""

        pool = squence.PR[index]
        scores = squence.PS[index]

        for i in range(len(pool)):
            c = SimilarityScore((pool[i] or "").strip(), res.strip())
            if c >= scores[i]:
                return ""
        return "#interesting-" + str(index)

    # ---------------------------------------------------------
    #  关键：真正发包的函数（JSON/TinyTuya + Hex/Socket）
    # ---------------------------------------------------------
    def sendMessage(self, message, retry=0):
        """
        方案A：timeout / 无回包 => 返回 ""（空串）
        """
        MAX_RETRY = 3

        # 兼容：raw 有字段但 headers 不包含
        has_tuya_hint = (
            ("DevID" in getattr(message, "headers", {})) or
            ("LocalKey" in getattr(message, "headers", {})) or
            ("DevID" in getattr(message, "raw", {})) or
            ("LocalKey" in getattr(message, "raw", {}))
        )

        # =============== 分支 1：TinyTuya JSON fuzz 模式 ===================
        if has_tuya_hint and self.tuya_dev_id:

            dev_id = str(message.raw.get("DevID", self.tuya_dev_id)).strip()
            address = str(message.raw.get("Address", self.tuya_address)).strip()
            local_key = str(message.raw.get("LocalKey", self.tuya_local_key)).strip()
            ver_str = str(message.raw.get("Version", str(self.tuya_version))).strip()
            try:
                version = float(ver_str)
            except ValueError:
                version = self.tuya_version

            config_changed = (
                dev_id != self.tuya_dev_id or
                address != self.tuya_address or
                local_key != self.tuya_local_key or
                version != self.tuya_version
            )
            if config_changed:
                self.tuya_dev_id = dev_id
                self.tuya_address = address
                self.tuya_local_key = local_key
                self.tuya_version = version
                self._invalidate_shared_tuya()

            self._init_tuya_device()
            if self.tuya_device is None:
                return "#error"

            cmd_str = message.raw.get("Cmd", None)
            if cmd_str is not None:
                try:
                    cmd = int(str(cmd_str).strip(), 0)
                except ValueError:
                    cmd = self.default_cmd
            else:
                cmd = self.default_cmd

            json_str = (message.raw.get("Content", "") or "").strip()
            if not json_str:
                return ""

            try:
                payload = tinytuya.MessagePayload(
                    cmd=cmd,
                    payload=json_str.encode("utf-8", errors="ignore")
                )

                resp = self.tuya_device._send_receive(payload)

                # ✅ 方案A：无回包/丢包 => ""（允许重试）
                if resp is None:
                    if retry < MAX_RETRY:
                        return self.sendMessage(message, retry + 1)
                    return ""

                return str(resp)

            except Exception as e:
                # 这里大多是协议/解密/网络异常，视为 error（避免误判 crash）
                print("TinyTuya error:", e)
                if retry < MAX_RETRY:
                    self._invalidate_shared_tuya()
                    self._init_tuya_device()
                    return self.sendMessage(message, retry + 1)
                return "#error"

        # =============== 分支 2：IP + Port + hex socket 模式 ===================
        if ("IP" in getattr(message, "headers", {})) and ("Port" in getattr(message, "headers", {})):
            ip = str(message.raw["IP"]).strip()
            port = int(message.raw["Port"])
            hex_str = str(message.raw.get("Content", "")).strip().replace(" ", "")
            print(hex_str)

            try:
                payload = bytes.fromhex(hex_str)
            except ValueError:
                print("Hex parse error in Content:", hex_str)
                return "#error"

            sock = None
            try:
                sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                sock.settimeout(2)
                sock.connect((ip, port))
                sock.sendall(payload)

                try:
                    resp_bytes = sock.recv(2048)
                except socket.timeout:
                    # ✅ 方案A：timeout => ""（允许重试）
                    if retry < MAX_RETRY:
                        return self.sendMessage(message, retry + 1)
                    return ""

                if not resp_bytes:
                    return ""

                return resp_bytes.hex()

            except socket.timeout:
                if retry < MAX_RETRY:
                    return self.sendMessage(message, retry + 1)
                return ""
            except Exception as e:
                print("Socket error:", e)
                return "#error"
            finally:
                if sock is not None:
                    try:
                        sock.close()
                    except Exception:
                        pass

        # =============== 两种信息都没有：输入文件不完整 ===================
        print("Error : IP/Port or DevID/LocalKey should be included in input files")
        return "#error"
