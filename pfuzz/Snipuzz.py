import getopt
import os
import sys
import time
import random

import pandas as pd
from scipy.cluster import hierarchy

sys.path.append(r'..')

from SnR import Messenger
from Seed import Message, Seed


# Golbal var
queue = []
restoreSeed = ''
outputfold = ''


# read the input file and store it as seed
def readInputFile(file):
    s = Seed()
    with open(file, 'r') as f:
        lines = f.read().split("\n")
    for i in range(0, len(lines)):
        if "========" in lines[i]:
            mes = Message()
            for j in range(i + 1, len(lines)):
                if "========" in lines[j]:
                    i = j
                    break
                if ":" in lines[j]:
                    mes.append(lines[j])
            s.append(mes)
    return s


# read the input fold and store them as seeds
def readInputFold(fold):
    seeds = []
    files = os.listdir(fold)
    for file in files:
        print("Loading file: ", os.path.join(fold, file))
        seeds.append(readInputFile(os.path.join(fold, file)))
    return seeds


# Write the probe result that has been run into the output
def writeRecord(queue, fold):
    with open(os.path.join(fold, 'ProbeRecord.txt'), 'w') as f:
        for i in range(len(queue)):
            f.writelines("========Seed " + str(i) + "========\n")
            for j in range(len(queue[i].M)):

                f.writelines("Message Index-" + str(j) + "\n")
                for header in queue[i].M[j].headers:
                    f.writelines(header + ":" + queue[i].M[j].raw[header] + '\n')
                f.writelines("\n")

                f.writelines('Original Response' + "\n")
                f.writelines(queue[i].R[j] if j < len(queue[i].R) else "")

                f.writelines('Probe Result:' + "\n")
                f.writelines('PI' + "\n")
                for n in queue[i].PI[j]:
                    f.write(str(n) + " ")
                f.writelines("\n")

                f.writelines('PR and PS' + "\n")
                for n in range(len(queue[i].PR[j])):
                    f.writelines("(" + str(n) + ") " + queue[i].PR[j][n])
                    f.writelines(str(queue[i].PS[j][n]) + "\n")

            f.writelines("\n\n")
    return 0


# Read the probe results from the record, thus skip the probe process and directly start the mutation test.
def readRecordFile(file):
    queue = []
    with open(os.path.join(file), 'r') as f:
        lines = f.readlines()
        i = 0
        while i <= len(lines) - 1:
            if lines[i].startswith("========Seed"):
                seedStart = i + 1
                seedEnd = len(lines)
                for j in range(i + 1, len(lines)):
                    if lines[j].startswith("========Seed"):
                        seedEnd = j
                        break

                seed = Seed()
                index = seedStart

                while index < seedEnd:
                    if lines[index].startswith('Message Index'):
                        message = Message()
                        responseStart = seedEnd
                        for j in range(index, seedEnd):
                            if lines[j].startswith('Original Response'):
                                responseStart = j
                                break
                        for line in lines[index + 1:responseStart - 1]:
                            message.append(line)
                        seed.M.append(message)
                        index = responseStart

                    if index < seedEnd and lines[index].startswith('Original Response'):
                        index += 1
                        if index < seedEnd:
                            seed.R.append(lines[index])

                    if index < seedEnd and lines[index].startswith('PI'):
                        index += 1
                        if index < seedEnd:
                            PIstr = lines[index]
                            PI = []
                            for n in PIstr.strip().split(' '):
                                if n.strip() != "":
                                    PI.append(int(n))
                            seed.PI.append(PI)

                    if index < seedEnd and lines[index].startswith('PR and PS'):
                        index += 1
                        ends = seedEnd
                        PR = []
                        PS = []
                        for j in range(index, seedEnd):
                            if lines[j].startswith('Message Index'):
                                ends = j
                                break
                        for j in range(index, ends):
                            if lines[j].startswith("("):
                                PR.append(lines[j][3:])
                            elif lines[j].strip() and lines[j][0].isdigit():
                                PS.append(float(lines[j].strip()))
                        seed.PR.append(PR)
                        seed.PS.append(PS)

                    index += 1

                i = seedEnd
                queue.append(seed)

            i += 1
    return queue


# DryRun：必须捕获 Messenger 返回的 "#error/#crash"
def dryRun(queue):
    global restoreSeed
    m = Messenger(restoreSeed)
    for i in range(0, len(queue)):
        seed = m.DryRunSend(queue[i])
        if isinstance(seed, str) and seed.startswith("#"):
            print("#### DryRun failed:", seed)
            return True
        queue[i] = seed
    return False


def EditDistanceRecursive(str1, str2):
    edit = [[i + j for j in range(len(str2) + 1)] for i in range(len(str1) + 1)]
    for i in range(1, len(str1) + 1):
        for j in range(1, len(str2) + 1):
            d = 0 if str1[i - 1] == str2[j - 1] else 1
            edit[i][j] = min(edit[i - 1][j] + 1, edit[i][j - 1] + 1, edit[i - 1][j - 1] + d)
    return edit[len(str1)][len(str2)]


def SimilarityScore(str1, str2):
    s1 = (str1 or "").strip()
    s2 = (str2 or "").strip()
    if len(s1) == 0 and len(s2) == 0:
        return 100.0
    max_len = max(len(s1), len(s2))
    if max_len == 0:
        return 0.0
    ED = EditDistanceRecursive(s1, s2)
    print("-------ED: ", ED, "max_len: ", max_len)
    return round((1 - (ED / max_len)) * 100, 2)


# Probe（方案A）：过滤空响应，避免污染 PR/PS/PI
def Probe(SeedObj):
    global restoreSeed

    print("*** Probe ")
    m = Messenger(restoreSeed)

    for index in range(len(SeedObj.M)):

        responsePool = []
        similarityScore = []
        probeResponseIndex = []

        print(SeedObj.M[index].raw["Content"].strip())

        response1 = m.ProbeSend(SeedObj, index)
        time.sleep(1)
        response2 = m.ProbeSend(SeedObj, index)

        # ✅ 方案A关键：任何一次为空，就给占位并跳过该 message 的 probe
        if (response1 or "").strip() == "" or (response2 or "").strip() == "":
            content_len = len(SeedObj.M[index].raw.get("Content", ""))
            SeedObj.PR.append([""])                  # 占位：空响应类
            SeedObj.PS.append([100.0])               # 占位阈值
            SeedObj.PI.append([0] * content_len)     # 全部归到 0 类
            continue

        responsePool.append(response1)
        similarityScore.append(SimilarityScore(response1.strip(), response2.strip()))

        # probe process: delete ith byte
        for i in range(0, len(SeedObj.M[index].raw["Content"])):
            temp = SeedObj.M[index].raw["Content"]
            SeedObj.M[index].raw["Content"] = SeedObj.M[index].raw["Content"].strip()[:i] + \
                                              SeedObj.M[index].raw["Content"].strip()[i + 1:]

            response1 = m.ProbeSend(SeedObj, index)
            time.sleep(1)
            _ = m.ProbeSend(SeedObj, index)  # response2 不再强依赖（避免噪声）
            print(response1, end='')

            # ✅ 方案A关键：空响应直接归入 0 类，不引入新类
            if (response1 or "").strip() == "":
                probeResponseIndex.append(0)
                SeedObj.M[index].raw["Content"] = temp
                continue

            flag = True
            for j in range(0, len(responsePool)):
                target = responsePool[j]
                score = similarityScore[j]
                c = SimilarityScore((target or "").strip(), response1.strip())
                if c >= score:
                    flag = False
                    probeResponseIndex.append(j)
                    print(str(j) + " ", end='')
                    sys.stdout.flush()
                    break

            if flag:
                responsePool.append(response1)
                # ✅ 新类阈值：用自己和自己（或下一次）比容易受噪声影响，这里用 100 作为保守阈值
                similarityScore.append(100.0)
                probeResponseIndex.append(len(responsePool) - 1)

            SeedObj.M[index].raw["Content"] = temp

        SeedObj.PR.append(responsePool)
        SeedObj.PS.append(similarityScore)
        SeedObj.PI.append(probeResponseIndex)

    return SeedObj


def getFeature(response, score):
    feature = {'a': 0, 'n': 0, 's': 0}
    response = (response or "")
    length = len(response)

    if length == 0:
        return [0, 0, 0, 0, score]

    cur = None
    pre = None

    for ch in response:
        if ch.isdigit():
            cur = 'n'
        elif ch.isalpha():
            cur = 'a'
        else:
            cur = 's'

        if pre is None:
            pre = cur
        elif pre != cur:
            feature[pre] = feature[pre] + 1
            pre = cur

    if cur in feature:
        feature[cur] = feature[cur] + 1

    return [feature['a'], feature['n'], feature['s'], length, score]


# ✅ 必改：修越界
def formSnippets(pi, cluster, index):
    snippet = []
    for i in range(index):
        c1 = int(cluster[i][0])
        c2 = int(cluster[i][1])
        p = int(cluster[i][3])
        for j in range(len(pi)):
            if pi[j] == c1 or pi[j] == c2:
                pi[j] = p

    i = 0
    while i < len(pi) - 1:
        j = i
        skip = True
        while j < len(pi) and skip:
            j += 1
            if j == len(pi) or pi[j] != pi[i]:
                snippet.append([i, j - 1])
                skip = False
        i = j

    return snippet


def interesting(oldSeed, index):
    global queue
    global restoreSeed
    m = Messenger(restoreSeed)

    print(oldSeed.M[index].raw["Content"])

    seed = Seed()
    seed.M = oldSeed.M
    seed = m.DryRunSend(seed)
    if isinstance(seed, str) and seed.startswith("#"):
        return
    seed = Probe(seed)
    queue.append(seed)


# ✅ 必改：修文件名
def writeOutput(seed):
    global outputfold
    ts = time.strftime("%Y%m%d-%H%M%S", time.localtime())
    file = f'Crash-{ts}.txt'

    with open(os.path.join(outputfold, file), 'w') as f:
        for i in range(len(seed.M)):
            f.writelines("Message Index-" + str(i) + "\n")
            for header in seed.M[i].headers:
                f.writelines(header + ":" + seed.M[i].raw[header] + '\n')
            f.writelines("\n")

    print("Found a crash @ " + ts)
    sys.exit()


def responseHandle(seed, info):
    if (info or "").startswith("#interesting"):
        print("~~Get Interesting in :")
        interesting(seed, int(info.split('-')[1]))
        return False

    if (info or "").startswith("#error"):
        print("~~Something wrong with the target infomation (e.g. IP addresss or port)")
        return True

    if (info or "").startswith("#crash"):
        writeOutput(seed)

    # 方案A：空串/普通串都直接继续
    return True


def SnippetMutate(seed, restoreSeedObj):
    m = Messenger(restoreSeedObj)

    for i in range(len(seed.M)):
        pool = seed.PR[i]
        poolIndex = seed.PI[i]
        similarityScores = seed.PS[i]

        featureList = []
        for j in range(len(pool)):
            featureList.append(getFeature((pool[j] or "").strip(), similarityScores[j]))

        df = pd.DataFrame(featureList)
        cluster = hierarchy.linkage(df, method='average', metric='euclidean')

        seed.ClusterList.append(cluster)

        mutatedSnippet = []
        for index in range(len(cluster)):
            snippetsList = formSnippets(poolIndex, cluster, index)
            for snippet in snippetsList:
                if snippet not in mutatedSnippet:
                    mutatedSnippet.append(snippet)
                    tempMessage = seed.M[i].raw["Content"]

                    # ========  BitFlip ========
                    print("--BitFlip")
                    message = seed.M[i].raw["Content"]
                    asc = ""
                    for o in range(snippet[0], snippet[1]):
                        asc = asc + (chr(255 - ord(message[o])))
                    message = message[:snippet[0]] + asc + message[snippet[1] + 1:]
                    seed.M[i].raw["Content"] = message
                    responseHandle(seed, m.SnippetMutationSend(seed, i))
                    seed.M[i].raw["Content"] = tempMessage

                    # ========  Empty ========
                    print("--Empty")
                    message = seed.M[i].raw["Content"]
                    message = message[:snippet[0]] + message[snippet[1] + 1:]
                    seed.M[i].raw["Content"] = message
                    responseHandle(seed, m.SnippetMutationSend(seed, i))
                    seed.M[i].raw["Content"] = tempMessage

                    # ========  Repeat ========
                    print("--Repeat")
                    message = seed.M[i].raw["Content"]
                    t = random.randint(2, 5)
                    message = message[:snippet[0]] + message[snippet[0]:snippet[1]] * t + message[snippet[1] + 1:]
                    seed.M[i].raw["Content"] = message
                    responseHandle(seed, m.SnippetMutationSend(seed, i))
                    seed.M[i].raw["Content"] = tempMessage

                    # ========  Interesting ========
                    print("--Interesting")
                    interestingString = ['on', 'off', 'True', 'False', '0', '1']
                    for t in interestingString:
                        message = seed.M[i].raw["Content"]
                        message = message[:snippet[0]] + t + message[snippet[1] + 1:]
                        seed.M[i].raw["Content"] = message
                        responseHandle(seed, m.SnippetMutationSend(seed, i))
                        seed.M[i].raw["Content"] = tempMessage

        seed.Snippet.append(mutatedSnippet)
    return 0


def Havoc(queue, restoreSeedObj):
    print("*Havoc")
    m = Messenger(restoreSeedObj)

    t = random.randint(0, len(queue) - 1)
    seed = queue[t]

    i = random.randint(0, len(seed.M) - 1)
    snippets = seed.Snippet[i]
    message = seed.M[i].raw["Content"]
    tempMessage = seed.M[i].raw["Content"]

    n = random.randint(0, len(snippets) - 1)
    snippet = snippets[n]

    pick = random.randint(0, 5)

    if pick == 0:  # BitFlip
        asc = ""
        for o in range(snippet[0], snippet[1]):
            asc = asc + (chr(255 - ord(message[o])))
        message = message[:snippet[0]] + asc + message[snippet[1] + 1:]
        seed.M[i].raw["Content"] = message
        temp = responseHandle(seed, m.SnippetMutationSend(seed, i))
        seed.M[i].raw["Content"] = tempMessage
        return temp

    elif pick == 1:  # Empty
        message = seed.M[i].raw["Content"]
        message = message[:snippet[0]] + message[snippet[1] + 1:]
        seed.M[i].raw["Content"] = message
        temp = responseHandle(seed, m.SnippetMutationSend(seed, i))
        seed.M[i].raw["Content"] = tempMessage
        return temp

    elif pick == 2:  # Repeat
        message = seed.M[i].raw["Content"]
        t = random.randint(2, 5)
        message = message[:snippet[0]] + message[snippet[0]:snippet[1]] * t + message[snippet[1] + 1:]
        seed.M[i].raw["Content"] = message
        temp = responseHandle(seed, m.SnippetMutationSend(seed, i))
        seed.M[i].raw["Content"] = tempMessage
        return temp

    elif pick == 3:  # Interesting
        interestingString = ['on', 'off', 'True', 'False', '0', '1']
        t = random.choice(interestingString)
        message = seed.M[i].raw["Content"]
        message = message[:snippet[0]] + t + message[snippet[1] + 1:]
        seed.M[i].raw["Content"] = message
        temp = responseHandle(seed, m.SnippetMutationSend(seed, i))
        seed.M[i].raw["Content"] = tempMessage
        return temp

    elif pick == 4:  # Random Bytes Flip
        start = random.randint(0, len(message) - 1)
        end = random.randint(start, len(message))
        asc = ""
        for o in range(start, end):
            asc = asc + (chr(255 - ord(message[o])))
        message = message[:start] + asc + message[end + 1:]
        seed.M[i].raw["Content"] = message
        temp = responseHandle(seed, m.SnippetMutationSend(seed, i))
        seed.M[i].raw["Content"] = tempMessage
        return temp

    return True


def getArgs(argv):
    inputfold = ''
    outputfold_local = ''
    restorefile = ''
    recordfile = ''
    try:
        opts, args = getopt.getopt(argv, "hi:r:o:c:", ["ifold=", "rfile=", "ofold=", "cfile="])
    except getopt.GetoptError:
        print('Snipuzz.py -i <inputfold> -r <restrefile> -o <outputfold> (-c <recordfile>)')
        sys.exit(2)
    for opt, arg in opts:
        if opt == '-h':
            print('test.py -i <inputfold> -r <restrefile> -o <outputfold> (-c <recordfile>)')
            sys.exit()
        elif opt in ("-i", "--ifold"):
            inputfold = arg
        elif opt in ("-r", "--rfile"):
            restorefile = arg
        elif opt in ("-o", "--ofold"):
            outputfold_local = arg
        elif opt in ("-c", "--cfile"):
            recordfile = arg
        if not recordfile:
            recordfile = 'unavailable'
    print('Input fold：', inputfold)
    print('Restore file: ', restorefile)
    print('Output fold：', outputfold_local)
    print('Record file：', recordfile)

    return inputfold, restorefile, outputfold_local, recordfile


def main(argv):
    global queue, restoreSeed, outputfold

    inputfold, restorefile, outputfold, recordfile = getArgs(argv)
    restoreSeed = readInputFile(restorefile)

    if recordfile and os.path.exists(recordfile):
        queue = readRecordFile(recordfile)
        for seed in queue:
            seed.display()
        if dryRun(queue):
            print('#### Dry run failed, check the inputs or connection.')
            sys.exit()
    else:
        queue = readInputFold(inputfold)
        if dryRun(queue):
            print('#### Dry run failed, check the inputs or connection.')
            sys.exit()
        for i in range(len(queue)):
            queue[i] = Probe(queue[i])
        writeRecord(queue, outputfold)

    skip = False
    while True:
        if not skip:
            i = 0
            while i < len(queue):
                if not queue[i].isMutated:
                    SnippetMutate(queue[i], restoreSeed)
                i += 1
        skip = True
        skip = Havoc(queue, restoreSeed)


if __name__ == "__main__":
    main(sys.argv[1:])
