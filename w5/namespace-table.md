# Namespace inode 對照表（Host PID 1 vs 容器 sleep）

> 取得方式：
> - 容器 host 視角 PID：`CPID=$(docker inspect -f '{{.State.Pid}}' ns-demo)`
> - 容器端：`sudo ls -la /proc/$CPID/ns/`
> - host 端：`sudo ls -la /proc/1/ns/`
> inode 即 symlink 後面那串數字，兩個行程某 namespace inode 相同 → 共用；不同 → 被隔開。

| Namespace | Host PID 1 inode | 容器 sleep inode | 一樣嗎？ | 說明 |
|---|---|---|---|---|
| pid  | `4026531836` | `4026532282` | 否 | 容器有獨立行程編號空間，sleep 在容器內是 PID 1 |
| net  | `4026531840` | `4026532284` | 否 | 容器有獨立網路堆疊（自己的 lo / eth0 / 路由 / iptables） |
| mnt  | `4026531841` | `4026532279` | 否 | 容器有獨立掛載視角與根目錄 / |
| uts  | `4026531838` | `4026532280` | 否 | 容器有獨立 hostname（container id 前 12 碼） |
| ipc  | `4026531839` | `4026532281` | 否 | 容器有獨立 System V IPC / POSIX mq |
| user | `4026531837` | `4026531837` | **是** | Docker 預設未開 user namespace，容器 root = host root |

## 重點結論

- 六種裡有 **五種（pid / net / mnt / uts / ipc）inode 與 host 不同** → 隔離生效，超過「至少四種不同」的要求。
- 唯一相同的是 **user**：因為 Docker 預設不啟用 user namespace，容器內的 UID 0 直接對映 host 的 UID 0。這正是「以 root 跑容器、又沒開 userns 時」逃逸風險的根源；要降風險可用 `--userns-remap` 或 rootless Docker。


