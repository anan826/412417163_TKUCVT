# W06｜Docker Image 與 Dockerfile

> 環境：Windows + VMware 上的 Ubuntu 22.04 VM（app VM，W03 建立）
> `docker --version` → Docker version 24.0.7, build afdd53b｜BuildKit 已啟用

---

## 映像組成

- **Layers 是什麼**：image 的「檔案內容」本體。每一層是一包唯讀的 tarball，記錄相對於上一層的檔案系統差異（新增/修改/刪除）。`FROM`、`RUN`、`COPY`、`ADD` 各會疊一層。容器跑起來時，這些唯讀層被 overlay2 疊成 lower，再加一層可寫的 upper（就是 W05 的 merged view）。同一個 base 的層在多個 image 之間共用，所以 pull 才會看到 `Already exists`。
- **Config 是什麼**：一份 JSON，存的是「怎麼跑」的 metadata，而不是檔案內容——`Cmd`、`Entrypoint`、`Env`、`WorkingDir`、`ExposedPorts`、`User` 等。`WORKDIR`、`ENV`、`USER`、`CMD`、`ENTRYPOINT` 改的就是這份 config。
- **Manifest 是什麼**：把上面兩者綁在一起的索引。它列出 config 的 digest，以及每一層的 digest 與大小，registry 靠它知道一個 image 由哪些 blob 組成、要不要下載。

---

## python:3.12-slim inspect 摘錄

`docker image inspect python:3.12-slim` 的關鍵欄位：

- **Config.Cmd**：`["python3"]`
- **Config.Env**：
  ```
  PATH=/usr/local/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin
  LANG=C.UTF-8
  GPG_KEY=7169605F62C751356D054A26A821E680E5FA6305
  PYTHON_VERSION=3.12.7
  PYTHON_SHA256=24887b92e2afd4a2ac602419ad4b596372f67ac9b077190f459aba390faf5550
  ```
- **Config.WorkingDir**：`""`（空字串，base image 沒設）
- **RootFS.Layers 數量**：4 層

---

## Layer 快取實驗

| 情境 | build 時間 |
|---|---|
| v1 首次 build | 41.2s |
| v1 改 app.py 後 rebuild | 38.7s |
| v2 首次 build | 40.5s |
| v2 改 app.py 後 rebuild | 1.9s |

觀察：v2 的 rebuild 快，是因為 `COPY app/requirements.txt .` 跟 `RUN pip install` 被排在「改 code 不會動到的位置」。改 `app.py` 只讓最後的 `COPY app/ .` 那層的 cache key 變動，前面那兩層的 cache key 完全沒變、直接命中快取，所以 pip install 一秒都不跑。v1 之所以慢，是因為 `COPY app/ .` 排在 pip install **前面**，而 `app.py` 屬於 `app/` 的一部分——這層一 miss，後面所有層（含 pip install）的 cache key 都連帶失效，整串重算。這正是「某層 miss 則其後全 miss」的代價。

---

## CMD vs ENTRYPOINT 實驗

| 寫法 | `docker run <img>` 輸出 | `docker run <img> extra1 extra2` 輸出 |
|---|---|---|
| CMD shell form | `argv = ['show_args.py', 'default1', 'default2']`；`PID = 7` | 忽略預設、執行 `/bin/sh -c extra1 extra2`，`sh: 1: extra1: not found`（exit 127） |
| CMD exec form | `argv = ['show_args.py', 'default1', 'default2']`；`PID = 1` | 整條 CMD 被覆蓋成 `extra1 extra2`，`docker: Error response... exec: "extra1": executable file not found in $PATH` |
| ENTRYPOINT + CMD | `argv = ['show_args.py', 'default1', 'default2']`；`PID = 1` | `argv = ['show_args.py', 'extra1', 'extra2']`；`PID = 1`（完美附加） |

結論：`ENTRYPOINT + CMD` 最穩，原因有二。第一，**參數行為符合直覺**：ENTRYPOINT 固定主程式（`python show_args.py`），`docker run` 後面接的東西只覆蓋 CMD 那段、變成附加參數，不會像純 CMD 那樣把整條命令吃掉。第二，**訊號處理正確**：exec form 讓 PID 1 就是 python 本身（上表 PID=1），容器收得到 `docker stop` 送的 SIGTERM 能 graceful shutdown；shell form 的 PID 1 是 `/bin/sh`，python 是它 fork 出來的子程序，SIGTERM 被 sh 吃掉、python 收不到，`docker stop` 會等滿 10 秒才 SIGKILL 強制殺。

---

## Multi-stage 大小對照

| Image | SIZE |
|---|---|
| python:3.12（builder base） | 1.02 GB |
| python:3.12-slim（runtime base） | 130 MB |
| myapp:v2（單階段） | 147 MB |
| myapp:multi（多階段） | 142 MB |

解釋：builder stage 那層用的是完整版 `python:3.12`（含 gcc、build-essential、各種 -dev header，整整 1 GB），但它的 layers **不會**進最終 image——最終 image 只疊 runtime stage（`python:3.12-slim`）的層，加上從 builder `COPY --from` 搬過來的 site-packages。所以 multi 比 v2 還略小一點（v2 在 slim 上跑 `pip install` 會留下 pip 的 cache/metadata 殘渣，multi 只搬乾淨的套件檔）。但那些 builder 層沒有真的消失：`docker images -a` 會看到 `<none>` 的中間 image，它們留在本機 build cache 裡下次可重用；只是 `docker push` 時不會被推上 registry。這就是 multi-stage「最終產物瘦、本機快取不瘦」的特性。flask 這種純 Python 套件差距不大，但若 app 要編譯 C extension（numpy、psycopg2 之類），差距會從幾 MB 拉到數百 MB。

---

## .dockerignore 故障注入

| 項目 | 故障前 | 故障中 | 回復後 |
|---|---|---|---|
| du -sh . | 88K | 151M | 152M（檔案還在本機，但被 ignore 排除） |
| build context 傳輸大小 | 6.14kB | 157.30MB | 4.81kB |
| build 時間 | 1.8s | 7.6s | 1.7s |

說明：故障中 `du` 與「故障前」差 150M，是因為注入了 100M 的假 `.git/objects/big.pack` 與 50M 的 `logs/huge.log`。注意「回復後」的 `du -sh .` 依然是 150M+（檔案實體還在硬碟上），但 build 的 `transferring context` 掉回 4.81kB——這證明 `.dockerignore` 是在「傳 context 給 daemon」這一步就把它們濾掉，垃圾根本沒被送進 build。

---

## 排錯紀錄

- **症狀**：把 `Dockerfile.multi` 設成最終 `Dockerfile` 並 build 後，`docker run myapp:multi`（不接 `app.py`）容器秒退，`docker logs` 顯示 `python: can't open file '//app.py': [Errno 2] No such file or directory`。
- **診斷**：最終版用 `ENTRYPOINT ["python"]` + `CMD ["app.py"]`，正常情況下兩者組合成 `python app.py`。但我 `docker run` 時手賤多打了一個自訂參數，把 CMD 的 `app.py` 覆蓋掉了，於是變成只執行 `python`（無檔名）→ 進 REPL 又無 TTY → 退出；而教材步驟 25 的 `docker run ... myapp:multi app.py` 是「故意重新指定 CMD」，剛好補回 `app.py`，所以那條能跑。換句話說，問題不在 image 而在我 run 的方式。另外確認 `WORKDIR /app` 生效、`app.py` 確實在 `/app` 下（`docker run --rm --entrypoint ls myapp:multi -l /app` 看得到）。
- **修正**：直接 `docker run -d --name myapp-multi -p 8081:80 -e APP_VERSION=multi myapp:multi`（不接任何參數，讓 CMD 的 `app.py` 生效）。
- **驗證**：`curl http://localhost:8081/` 回 `Hello from <id> | version=multi`；`docker exec myapp-multi whoami` 回 `appuser`（確認非 root 也正常）。

---

## 設計決策

**為什麼 runtime 選 `python:3.12-slim` 而不是 `alpine`？**

slim 用的是 glibc，跟 PyPI 上絕大多數預編譯的 manylinux wheel 相容——`pip install flask`（甚至 numpy、pandas）能直接抓二進位 wheel，不用在容器裡編譯。alpine 用的是 musl libc，many wheel 不相容，pip 常常會 fallback 去「從原始碼編譯」，這時又得 `apk add gcc musl-dev python3-dev`，不但拖慢 build，還可能踩到 musl 與某些套件的相容性坑。對這個 hello world app，alpine 確實能再小個幾十 MB，但換來的是 build 變慢、相容風險上升，划不來；對有 C extension 的 app 更是直接勸退。slim 已經比完整版小了快 8 倍（130MB vs 1GB），同時保住 glibc 生態相容性，是這個情境下「小」與「穩」的最佳平衡點。

**為什麼最終 `Dockerfile` 用 multi-stage 而非單階段 v2？**

編譯工具只在 build 時需要、runtime 用不到。multi-stage 把 `python:3.12`（含編譯器）關在 builder stage，最終 image 只留 slim runtime + 乾淨套件，既縮小了體積、也縮小了攻擊面（runtime 沒有 gcc，攻擊者進來也較難就地編譯東西）。再加 `USER appuser` 降權，是 W12 安全要求的提前鋪路。

---

## 可重跑最小命令鏈

```bash
cd ~/virt-container-labs/w06
docker build -f Dockerfile.multi -t myapp:multi .
docker run -d --name myapp-final -p 8080:80 -e APP_VERSION=final myapp:multi app.py
curl http://localhost:8080/
docker stop myapp-final && docker rm myapp-final
```
