# F1 Telemetry Raw Archive v1 / v2

所有整数使用 little-endian。新采集默认写 v2，文件名继续使用 `raw_packets.bin`。压缩只改变存储方式，UDP payload、UTC 纳秒接收时间、单调时钟、来源地址和端口均保留。v0.7+ 自动读取 v1 和 v2；v0.6 及更早版本不能读取 v2，可先生成 v1 还原副本。

## 文件头（16 字节）

结构：`<8sHHI`。

| 字段 | 字节 | 值 |
|---|---:|---|
| magic | 8 | `F1TLRAW\0` |
| archive_version | 2 | 1=旧格式；2=分块格式 |
| header_size | 2 | 16 |
| flags | 4 | 0，未知值拒绝读取 |

## 原始记录（v1，也作为 v2 块内内容）

结构：`<4sBBHQQ16sHII`，共 50 字节，然后紧接 payload。

| 字段 | 字节 | 值 |
|---|---:|---|
| magic | 4 | `PKT1` |
| record_version | 1 | 1 |
| address_family | 1 | 4 或 6 |
| header_size | 2 | 50 |
| received_at_ns | 8 | UTC 本地接收时间 |
| monotonic_ns | 8 | 本地单调时钟 |
| address | 16 | IPv4 前 4 字节有效、剩余补零；IPv6 全部有效 |
| source_port | 2 | 来源端口 |
| payload_length | 4 | 0–65535 |
| payload_crc32 | 4 | 原始 UDP payload 的 CRC32 |

## v2 块头（52 字节）

结构：`<4sBBHIIIIQQQI`，块头之后是 stored_length 字节的数据。

| 字段 | 字节 | 值 |
|---|---:|---|
| magic | 4 | `BLK2` |
| block_version | 1 | 1 |
| codec | 1 | 0=原样存储；1=zlib 封装 DEFLATE |
| header_size | 2 | 52 |
| stored_length | 4 | 块在磁盘上的数据字节数 |
| original_length | 4 | 解压后记录字节数，上限 8 MiB |
| record_count | 4 | 块内记录条数 |
| block_crc32 | 4 | 解压后整个记录区的 CRC32 |
| logical_start | 8 | 本块首条记录在虚拟 v1 文件内的偏移 |
| first_received_at_ns | 8 | 本块首条记录的 UTC 纳秒 |
| last_received_at_ns | 8 | 本块末条记录的 UTC 纳秒 |
| header_crc32 | 4 | 本块头前 48 字节的 CRC32 |

默认块原始数据上限 256 KiB，默认最多 256 个记录。原始字节压缩后变大时，自动使用 codec=0。本块解压长度有上限，解码器验证头 CRC、长度、记录数、首尾时间、块 CRC 及每包 CRC 后才交出该块记录。

## 偏移和索引

`ArchivedPacket.offset` 和数据库 `raw_offset` 始终表示逻辑 v1 偏移（从文件头的 16 开始）。v1 中该值同时也是实际文件偏移；v2 中不能用它直接 seek 磁盘文件。每个 v2 记录另外保存 `raw_block_offset`（物理块头偏移）和 `raw_block_record_offset`（解压区内偏移）。`read_packet_at` 利用这两个位置只解压所在块，不需要遍历整个文件。

旧文件转换出的逻辑偏移不变，但是物理块位置属于新的副本。转换工具保留旧文件和索引，不修改旧数据库；不要直接用压缩副本覆盖旧 Raw，之后继续使用旧物理索引。

## 刷盘、异常与恢复

按记录数或块字节上限写出块；索引批量提交前先 flush + fsync Raw。停止收到数据后，写线程在空闲约一秒时写出尾块。正常关闭先排空写入队列，再写出尾块并结束数据库会话。

普通 Replay 严格报错，不忽略截断、CRC 错误和未知格式。`--recover-tail` 可将截断尾部之前的完整记录/完整块恢复到新的文件，并明确返回 `recovered_prefix` 状态。CRC 损坏和错误块头不会当作可恢复截断。若文件恰在完整块边界被截掉，只看 Raw 无法知道原本应有多少后续包，必须结合 metadata/数据库包数核对。

强制终止或断电可能损失尚未提交的尾块或队列内容；此设计保证已经写完并通过校验的块能够独立恢复，不承诺突然断电零丢包。CRC32 用于发现损坏，不属于密码学完整性认证。

## 安全转换

`python -m collector.compress_archive SOURCE --output NEW_FILE` 逐包校验源文件，生成临时副本，然后再次逐包比较两份文件中的 payload、时间、来源、CRC 和逻辑偏移。验证完成后原子发布到此前不存在的目标文件。目标已存在、源文件在转换中变化或校验失败时拒绝发布，源文件始终保留。

`--restore` 生成不压缩的 v1 副本；`--recover-tail` 明确允许对截断文件恢复完整前缀。转换会暂时同时占用原文件和副本空间；这是校验发布过程的需要。
