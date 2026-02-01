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
            if str1[i - 1] == str2[j - 1]:
                d = 0
            else:
                d = 1
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

        # Socket 模式用的 sender
        self.SocketSender = None

        # 如果 restoreSeed 里有 DevID / LocalKey，则初始化 TinyTuya
        if restoreSeed and getattr(restoreSeed, "M", None) and restoreSeed.M:
            cfg = restoreSeed.M[0].raw
            if "DevID" in cfg and "LocalKey" in cfg:
                self.tuya_dev_id = cfg["DevID"].strip()
                # Address 优先，没有就退回 IP
                self.tuya_address = cfg.get("Address", cfg.get("IP", "")).strip()
                self.tuya_local_key = cfg["LocalKey"].strip()
                ver_str = cfg.get("Version", "3.4").strip()
                try:
                    self.tuya_version = float(ver_str)
                except ValueError:
                    self.tuya_version = 3.4

                # ⭐ 如果 restoreSeed 本身写了 Cmd，就用来作为默认 cmd
                if "Cmd" in cfg:
                    try:
                        # base=0，支持 "13" / "0x0d" 等写法
                        self.default_cmd = int(str(cfg["Cmd"]).strip(), 0)
                    except ValueError:
                        self.default_cmd = 13

                self._init_tuya_device()

    def _init_tuya_device(self):
        """根据当前 tuya_* 配置初始化 TinyTuya Device（只真正 init 一次）"""
        if not (self.tuya_dev_id and self.tuya_address and self.tuya_local_key):
            print("TinyTuya config incomplete, Tuya mode disabled.")
            self.tuya_device = None
            return

        # 如果已经有共享设备，就直接复用
        if Messenger.shared_tuya_device is not None:
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
        self.tuya_device = device

    # ---------------------------------------------------------
    #  Snipuzz 调用：DryRun 阶段
    # ---------------------------------------------------------
    def DryRunSend(self, squence):
        """
        DryRun：先把当前 seed 的所有 message 发一遍，
        再把 restoreSeed 的所有 message 发一遍，确认环境 OK。
        """
        # 1) 发当前 seed 的报文
        for message in squence.M:
            response = self.sendMessage(message)
            if response == "#error":
                return True
            squence.R.append(response)

        # 2) 发 restoreSeed 的恢复报文
        if self.restore and getattr(self.restore, "M", None):
            for message in self.restore.M:
                response = self.sendMessage(message)
                if response == "#error":
                    return True

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
        # 1) 发送 fuzz 目标序列
        for i in range(len(squence.M)):
            response = self.sendMessage(squence.M[i])
            if response == "#error":
                return "#error"
            elif response == "#crash":
                return "#crash"
            if i == index:
                res = response

        # 2) 发送 restore 序列
        if self.restore and getattr(self.restore, "M", None):
            for i in range(len(self.restore.M)):
                restoreResponse = self.sendMessage(self.restore.M[i])
                if restoreResponse == "#error":
                    return "#error"
                elif restoreResponse == "#crash":
                    return "#crash"

        return res

    # ---------------------------------------------------------
    #  Snipuzz 调用：SnippetMutate 阶段
    # ---------------------------------------------------------
    def SnippetMutationSend(self, squence, index):
        """
        SnippetMutate 阶段发送序列，并根据响应与 PR/PS 判断是否 #interesting
        """
        res = ""
        # 1) 发送 fuzz 目标序列
        for i in range(len(squence.M)):
            response = self.sendMessage(squence.M[i])
            if response == "#error":
                return "#error"
            elif response == "#crash":
                return "#crash"
            if i == index:
                res = response

        # 2) 发送 restore 序列
        if self.restore and getattr(self.restore, "M", None):
            for i in range(len(self.restore.M)):
                restoreResponse = self.sendMessage(self.restore.M[i])
                if restoreResponse == "#error":
                    return "#error"
                elif restoreResponse == "#crash":
                    return "#crash"

        # 3) 和已有响应池比较相似度，决定是不是 #interesting
        pool = squence.PR[index]
        scores = squence.PS[index]

        for i in range(len(pool)):
            c = SimilarityScore(pool[i].strip(), res.strip())
            if c >= scores[i]:
                # 和某一类已有响应足够相似 → 不算 interesting
                return ""
        # 没有归入任何一类 → 新响应 → interesting
        return "#interesting-" + str(index)

    # ---------------------------------------------------------
    #  关键：真正发包的函数（JSON/TinyTuya + Hex/Socket）
    # ---------------------------------------------------------
    def sendMessage(self, message, retry=0):
        """
        Snipuzz 调用的底层发送函数：
        - 如果 header 里有 DevID/LocalKey 且 TinyTuya 配置完整 → TinyTuya JSON 模式
        - 否则如果有 IP/Port → 原来的 TCP+hex 模式

        retry: 当前重试次数，用来判断是否 #crash
        """
        MAX_RETRY = 3

        # =============== 分支 1：TinyTuya JSON fuzz 模式 ===================
        if ("DevID" in message.headers or "LocalKey" in message.headers) and self.tuya_dev_id:

            # 每条 message 可以覆盖默认设备配置（一般不会变）
            dev_id = message.raw.get("DevID", self.tuya_dev_id).strip()
            address = message.raw.get("Address", self.tuya_address).strip()
            local_key = message.raw.get("LocalKey", self.tuya_local_key).strip()
            ver_str = message.raw.get("Version", str(self.tuya_version)).strip()
            try:
                version = float(ver_str)
            except ValueError:
                version = self.tuya_version

            # 如果配置变化，更新并重新 init（共享 device 也会更新）
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
                # 重设共享设备（下一次 _init 再建）
                Messenger.shared_tuya_device = None

            # 确保 device 已初始化
            self._init_tuya_device()
            if self.tuya_device is None:
                return "#error"

            # ⭐ 读取 Cmd：优先用当前 message 的 Cmd，没有就用 default_cmd
            cmd_str = message.raw.get("Cmd", None)
            if cmd_str is not None:
                try:
                    # base=0 支持 "13" / "0x0d"
                    cmd = int(str(cmd_str).strip(), 0)
                except ValueError:
                    cmd = self.default_cmd
            else:
                cmd = self.default_cmd

            # Snipuzz 变异后的 JSON 字符串
            json_str = message.raw.get("Content", "")
            json_str = (json_str or "").strip()

            if not json_str:
                # 空 payload，当成没回包
                return ""

            try:
                payload = tinytuya.MessagePayload(
                    cmd=cmd,  # ⭐ 改成动态 cmd
                    payload=json_str.encode("utf-8", errors="ignore")
                )

                resp = self.tuya_device._send_receive(payload)

                # 设备完全不回包：允许重试几次，再认定 crash
                if resp is None:
                    if retry < MAX_RETRY:
                        return self.sendMessage(message, retry + 1)
                    else:
                        return "#crash"

                return str(resp)

            except Exception as e:
                print("TinyTuya error:", e)
                if retry < MAX_RETRY:
                    # 出错时试着重新 init 再重发
                    Messenger.shared_tuya_device = None
                    self._init_tuya_device()
                    return self.sendMessage(message, retry + 1)
                else:
                    # 连续多次异常 → 当成 crash
                    return "#crash"

        # =============== 分支 2：原来的 IP + Port + hex socket 模式 ===================
        if "IP" in message.headers and "Port" in message.headers:
            ip = message.raw["IP"].strip()
            port = int(message.raw["Port"])
            hex_str = message.raw["Content"].strip().replace(" ", "")
            print(hex_str)

            try:
                payload = bytes.fromhex(hex_str)
            except ValueError:
                print("Hex parse error in Content:", hex_str)
                return "#error"

            try:
                self.SocketSender = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                self.SocketSender.settimeout(2)
                self.SocketSender.connect((ip, port))

                self.SocketSender.sendall(payload)

                try:
                    resp_bytes = self.SocketSender.recv(2048)
                except socket.timeout:
                    # 收包超时：允许重试几次，再认定 crash
                    if retry < MAX_RETRY:
                        return self.sendMessage(message, retry + 1)
                    else:
                        return "#crash"

                if not resp_bytes:
                    # 对 fuzz 来说，没回包当空响应
                    return ""

                return resp_bytes.hex()

            except socket.timeout:
                if retry < MAX_RETRY:
                    return self.sendMessage(message, retry + 1)
                else:
                    return "#crash"
            except Exception as e:
                print("Socket error:", e)
                # 这里也可以视情况当 crash 或 error，我保持原来风格
                return "#error"

        # =============== 两种信息都没有：输入文件不完整 ===================
        print("Error : IP/Port or DevID/LocalKey should be included in input files")
        return "#error"
