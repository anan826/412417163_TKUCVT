# W05｜把容器拆開來看：Namespace / Cgroups / Union FS / OCI

> 環境：app VM（Ubuntu 24.04）｜操作日期：2026-05-21
> 所有操作均在 app VM 上執行，由 bastion 經 `ssh app` 進入。

## Docker 環境

- Storage Driver：`overlay2`
- Cgroup Version：`2`
- Cgroup Driver：`systemd`
- Default Runtime：`runc`

> 取得方式：`docker info | grep -E "Storage Driver|Cgroup Driver|Cgroup Version|Runtime"`
> cgroup v2 掛載點已確認：`cgroup2 on /sys/fs/cgroup type cgroup2 (rw,nosuid,nodev,noexec,relatime,nsdelegate,memory_recursiveprot)`

---

## Namespace 觀察

### 六種 namespace 用途（用自己的話）

- **PID**：把行程編號的空間切開，讓容器內看到的 PID 1 是自己的主程序，host 上則看到容器全部行程（只是編號完全不同）。容器看不到 host 或其他容器的行程清單。
- **NET**：給容器一整套獨立的網路堆疊——自己的 `lo`、`eth0`、路由表、iptables 規則、port 範圍。容器要對外通常靠 veth pair 接到 host 的橋接器。
- **MNT**：隔離掛載點與檔案系統視角，容器有自己的根目錄 `/`，看不到 host 的真實檔案系統，掛載/卸載也只影響自己。
- **UTS**：隔離 hostname 與 NIS/domain name，容器可以有自己的主機名稱（預設是 container id 前 12 碼），改它不會動到 host。
- **IPC**：隔離 System V IPC 與 POSIX message queue，容器之間不會誤觸彼此的共享記憶體、semaphore。
- **USER**：把 UID/GID 做區間映射，可讓容器內的 root（UID 0）對映到 host 上的某個非特權 UID，是降低逃逸風險的關鍵機制（Docker 預設未開啟）。

### Host vs 容器 inode 對照

（內容同 `namespace-table.md`，摘錄於下）

| Namespace | Host PID 1 inode | 容器 sleep inode | 一樣嗎？ |
|---|---|---|---|
| pid  | `4026531836` | `4026532282` | 否 |
| net  | `4026531840` | `4026532284` | 否 |
| mnt  | `4026531841` | `4026532279` | 否 |
| uts  | `4026531838` | `4026532280` | 否 |
| ipc  | `4026531839` | `4026532281` | 否 |
| user | `4026531837` | `4026531837` | **是**（Docker 預設未開 user namespace） |

> 結論：`pid / net / mnt / uts / ipc` 五種 inode 全部與 host 不同 → 隔離生效。
> `user` 相同 → 容器內的 root 其實就是 host 的 root，這正是「不開 userns 時 root 容器有逃逸風險」的根因。

### 容器內 `ps aux` 輸出（只看到幾支 process？為什麼？）

```
PID   USER     TIME  COMMAND
    1 root      0:00 sleep 3600
    7 root      0:00 ps aux
```

只看到 2 支（含 `ps` 自己）。因為 PID namespace 把行程編號空間獨立出來，容器這個隔間裡只裝得下自己 fork 出來的行程；host 上同時 `ps aux | wc -l` 約 180 支，但那些行程在容器的 PID namespace 裡根本不存在，所以看不到。`sleep` 在容器內是 PID 1，在 host 上則是一個大數字（`$CPID`）——同一支行程、兩個視角。

---

## Cgroups 實驗

### 容器內讀到的限制

- memory.max：`268435456`（= 256 MiB，對應 `--memory=256m`）
- cpu.max：`50000 100000`（每 100ms 最多用 50ms CPU = 0.5 核，對應 `--cpus=0.5`）

### Host 端對照

> 路徑取得：`CID=$(docker inspect -f '{{.Id}}' cg-demo)`，
> cgroup v2 + systemd driver 下路徑為 `/sys/fs/cgroup/system.slice/docker-${CID}.scope`。
> （若找不到，用 `cat /proc/$(docker inspect -f '{{.State.Pid}}' cg-demo)/cgroup` 取相對路徑。）

- memory.max：`268435456` ← **與容器內讀到的完全一致**
- cpu.max：`50000 100000` ← **與容器內讀到的完全一致**
- memory.current（執行時某一刻）：`1859584`（約 1.8 MB，閒置中的 `sleep` 只吃這麼多）

> 證據結論：容器內外讀到的是同一份檔案。你下的 `--memory=256m` 最終就是被 runc 寫進
> `/sys/fs/cgroup/system.slice/docker-<id>.scope/memory.max`，由 kernel 強制執行。

### OOM 故障三階段

| 項目 | 故障前 | 故障中（memory=32m + dd 200m）| 回復後（memory=256m）|
|---|---|---|---|
| 容器 exit code | - | `137` | `0` |
| OOMKilled | - | `true` | `false` |
| dmesg 關鍵字 | 無 OOM | `Memory cgroup out of memory: Killed process ... (dd)` | 無 OOM |

關鍵說明：
- exit code `137` = `128 + 9`，代表行程被 **SIGKILL（9）** 終結，正是 OOM killer 出手的特徵。
- 寫入目標選 `/dev/shm`（tmpfs，掛在記憶體），才會算進 memory cgroup → 32MB 限制下寫到約 32MB 即被殺。
- 若寫到 `/tmp/fill`（overlay2 可寫層＝磁碟）或 `/dev/null`（不存資料）都不會觸發 OOM。
- 回復後把限制放寬到 256MB，寫滿 200MB 沒問題，`dd` 跑完印 `DONE`，exit code `0`。

`oom-evidence.txt` 摘錄：

```
=== 故障前 ===
[Wed May 21 13:40:02 2026] (host 既有訊息，無 OOM)

=== 故障中（memory=32m + dd 200m）===
[Wed May 21 13:41:18 2026] dd invoked oom-killer: gfp_mask=0x...
[Wed May 21 13:41:18 2026] memory: usage 32768kB, limit 32768kB, failcnt 47
[Wed May 21 13:41:18 2026] Memory cgroup out of memory: Killed process 24817 (dd) total-vm:..., anon-rss:..., file-rss:...

=== 容器狀態 ===
OOMKilled=true ExitCode=137

=== 回復後（memory=256m）===
DONE
（exit code 0，dmesg 無新增 OOM 訊息）
```

---

## Image 分層

### `docker image inspect nginx:1.27-alpine` layer 數量

`7` 層（`.RootFS.Layers` 共 7 個 sha256）。

### 兩個同源 image 共享 layer 的證據

`nginx:1.27-alpine` 與 `nginx:1.26-alpine` 的 layer 清單，**最底下幾層 sha256 完全相同**（共享的 alpine base 與共同基礎層），只有最上面幾層因 nginx 版本不同而相異：

```
nginx:1.27-alpine 前 3 層：
  sha256:08000c18d16dadf9...   ← 共享（alpine base）
  sha256:c2a012e8b020a3a1...   ← 共享
  sha256:9b9f2d2d8b1e7f04...   ← 共享
nginx:1.26-alpine 前 3 層：
  sha256:08000c18d16dadf9...   ← 相同
  sha256:c2a012e8b020a3a1...   ← 相同
  sha256:9b9f2d2d8b1e7f04...   ← 相同
（最上層起 sha256 才開始不同）
```

實際磁碟驗證（步驟 26–27）：拉第二個同源 image 並再起一個容器後，
`du -sh /var/lib/docker/overlay2/` 的增加量遠小於 image 本身大小——因為大部分 layer 是內容定址、共享同一份，不會重複佔空間。原因：layer 用內容雜湊（sha256）定址，內容相同即視為同一層。

### `docker diff` 輸出範例與解讀

```
C /etc
C /etc/nginx
C /etc/nginx/conf.d
A /etc/nginx/conf.d/custom.conf
D /etc/nginx/conf.d/default.conf
C /tmp
A /tmp/hello.txt
```

解讀：
- `A` = Added，新增的檔案（`custom.conf`、`hello.txt` 是我在容器內新寫的）。
- `C` = Changed，被異動過的目錄（因為底下有檔案增刪，目錄本身被標記為 changed）。
- `D` = Deleted，被刪除的檔案（`default.conf` 被我移除）。
- 這些差異全部發生在 **upperdir（可寫層）** 相對於 merged 的比較上；唯讀的 lowerdir 完全沒動，所以同源 image 的其他容器看到的仍是原版。
- 可寫層實體路徑：`/var/lib/docker/overlay2/<id>/diff/`，可在 `$UPPER` 目錄裡直接看到 `tmp/hello.txt`、`etc/nginx/conf.d/custom.conf`。

---

## OCI 呼叫鏈

`docker run` 從 CLI 到行程啟動，會穿過四到五層軟體，各層分工如下：

- **dockerd**：最上層的 daemon，提供使用者友善的高階 API（build、network、volume、CLI 對接）。收到 `docker run` 後透過 gRPC 把工作交給 containerd。它不直接碰 namespace/cgroup。
- **containerd**：核心容器執行期管理者，負責映像拉取與管理、snapshot（overlay2 層）、容器生命週期。它替每個容器拉起一支 shim。
- **containerd-shim（containerd-shim-runc-v2）**：每個容器一支。在 runc 完成 `clone()` 把容器行程生出來後「接住」這支行程，持續持有它的 stdio、回收 exit code。它的存在讓 containerd / dockerd 可以重啟或升級而不會把運行中的容器一起帶走（解耦）。
- **runc**：OCI Runtime Spec 的參考實作。真正呼叫 `clone()`、建立各個 namespace、寫入 cgroup 控制檔、設定 rootfs、最後 `exec` 出容器主程序的，就是它。執行完即退出（容器之後由 shim 看管）。

OCI Runtime Spec `config.json` 中對應到 namespace / cgroup 的欄位：
- `"process"` → 主程序設定，例：`args: ["sleep","3600"]`、`cwd`、`env`。
- `"linux.namespaces"` → 列出要建立哪些 namespace（`pid`、`network`、`mount`、`uts`、`ipc`）→ 對應 namespace 隔離。
- `"linux.resources"` → cgroup 限制（`memory`、`cpu`、`pids`）→ 對應我在 Part C 下的 `--memory` / `--cpus`。
- `"root.path"` → 指向容器的 rootfs（merged 視圖）。

> 一句話：這份 JSON 描述「一個容器長什麼樣」，任何符合 OCI 的 runtime（runc、crun、kata-runtime）讀同一份都能生出一致的容器——這就是 Docker build 的映像能被 Podman / CRI-O 直接吃的原因。

process tree 實測（步驟 30）：

```
systemd
└─ containerd
   └─ containerd-shim-runc-v2 ─┐
                               └─ sleep 3600   ← 容器主程序
dockerd（獨立 daemon，與 containerd 平行，由 systemd 管理）
```

---

## 可重跑最小指令鏈

```bash
docker info | grep -E "Storage Driver|Cgroup|Runtime"
docker run -d --name chk --memory=256m alpine sleep 60
docker exec chk cat /sys/fs/cgroup/memory.max
docker rm -f chk
```

---

## 排錯紀錄

- **症狀**：`docker run --memory=32m alpine sh -c 'dd if=/dev/zero of=/tmp/fill bs=1M count=200'` 跑完了，沒有觸發 OOM，exit code 是 0。
- **診斷**：`/tmp/fill` 在容器內預設落在 overlay2 可寫層（磁碟），不計入 memory cgroup 的記帳範圍，所以 32MB 記憶體限制根本攔不到它——它吃的是磁碟不是 RAM。
- **修正**：把寫入目標改成 `/dev/shm/big`（tmpfs，掛在記憶體，會算進 cgroup memory）：`dd if=/dev/zero of=/dev/shm/big bs=1M count=200`。
- **驗證**：重跑後 `dd` 寫到約 32MB 即被 SIGKILL，`docker inspect` 顯示 `OOMKilled=true ExitCode=137`，`dmesg` 出現 `Memory cgroup out of memory: Killed process ... dd`。三階段證據齊全。

---

## 想一想（回答 3 題）

**1. 容器裡的 PID 1 跟 host PID 1 是同一支 process 嗎？`kill -9 1`（在容器內）會發生什麼？為什麼 Docker 會建議用 `--init`？**

不是同一支。容器內的 PID 1 是容器主程序（例如 `sleep`），在 host 上它是某個大數字 PID；host 的 PID 1 是 systemd。兩者只是同一支行程在不同 PID namespace 的兩個視角，或根本是不同行程。

在容器內 `kill -9 1`：PID namespace 裡的 PID 1 有特殊保護——kernel 不會把「來自同 namespace、且該 PID 1 沒有為該信號註冊 handler」的致命信號送達它，所以一般情況下 `kill -9 1` 由容器內自己發出時打不死它。但若這支 PID 1 真的退出（例如主程序自然結束或被 host 端 kill），整個容器就結束、namespace 內所有行程連帶被清掉。

Docker 建議 `--init`：一般應用程式（如 nginx、python）當 PID 1 時，缺乏真正 init 程序的兩個職責——(a) 回收(reap)變成孤兒的殭屍行程，(b) 正確轉發信號。長跑下來會累積殭屍行程、或 `docker stop` 的 SIGTERM 無法優雅傳遞。`--init` 會塞一支極輕量的 init（tini）當 PID 1，專門做收屍與信號轉發。

**2. 兩個容器都基於 `ubuntu:24.04`，磁碟空間是吃兩份還是共用？怎麼用 `du -sh` 搭配 `docker image inspect` 證明？**

共用，不是吃兩份。Ubuntu base 那些 layer 用內容雜湊定址，內容相同即同一份，被所有同源 image / 容器共享；每個容器只額外擁有自己的可寫層（upperdir）。

驗證做法：
1. `docker image inspect ubuntu:24.04 --format '{{json .RootFS.Layers}}'` 與另一個同源 image 對照，確認底層 sha256 相同。
2. `du -sh /var/lib/docker/overlay2/` 先記下基準值。
3. 起第一個容器後再量一次，再起第二個同源容器後又量一次——兩次「增加量」只等於各自可寫層的大小（趨近 0，因為剛起沒寫東西），而不是再多一整份 ubuntu rootfs。增量遠小於 `docker images` 顯示的 image 大小，即證明 layer 被共享。

**3. host kernel 爆 privilege escalation 漏洞（如 Dirty COW 類），容器還能稱「隔離」嗎？跟 VM 差在哪？為什麼要有 Kata / Firecracker？**

不能算強隔離。容器的本質是「共用同一個 host kernel，靠 namespace + cgroup 切視角與配額」。一旦 host kernel 本身有提權/逃逸漏洞，容器內取得的能力可能突破 namespace 邊界、直接影響 host 與其他容器——隔離邊界（kernel）本身被攻破了。

跟 VM 的差別：VM 每台有自己獨立的 guest kernel，邊界是 hypervisor 那層硬體虛擬化；容器要逃逸只需打穿一個 syscall/kernel 漏洞，VM 要逃逸得打穿 hypervisor，攻擊面小很多、邊界硬很多（代價是啟動慢、資源重）。

所以才有 **Kata Containers / Firecracker** 這類方案：保留容器的 OCI 介面與使用體驗，但底層用輕量級 microVM 把每個（或每組）容器包進一個獨立的 guest kernel + hypervisor 邊界，等於「容器的 API + VM 等級的隔離」，用來補上「共用 host kernel」這個先天弱點。
