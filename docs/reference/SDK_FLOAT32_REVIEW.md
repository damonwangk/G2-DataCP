# LeRobot float32 夹爪端点与当前 SDK 范围判断

2026-10-30 只读核查。没有导入/初始化 SDK，没有调用机器人控制接口。

结论：保留回放中的原始 `action` 数值；**不需要新增取整、最短十进制恢复或裁剪**。`float32(0.025)` 升为 Python double 后显示为 `0.02500000037252903`，当前 SDK 的范围判断会将输入与上界都转换为 float32，两者相等，端点不会仅因为这个尾数被拒绝。

## 当前机器人文件证据

- `/home/agi/app/gdk/version`：GDK 3.1.1，commit `8879e2d`。
- `/home/agi/app/gdk/build_dep/cpp/aarch64/include/common/types.h:309`：`JointServoControlReq.joint_positions` 为 `std::vector<double>`。
- `/home/agi/app/gdk/config/mc_impl_config.json`：左右 Dahuan 夹爪位置范围均为 `0.0` 到 `0.025`。
- `/home/agi/app/lib/libgdk_core.so` SHA256：`545053d9dd9d9aea3c37256b1f7592b0a5975263a1516dab36a8a0265322eac1`。
- `JointServoControlRequest` 的 `0xe9638` 从请求读取 double；`0xe9640` 的 `fcvt s0,d0` 将其转为 float32；`0xe9644` 调用 `MCImplConfigParser::ValidateParam(..., float)`。
- 普通关节分支在 `0x96f60/0x96f80` 使用 JSON `value<float>` 读取上下界，随后 float32 比较，`b.mi` 和 `b.gt` 只在小于最小值/大于最大值时转入拒绝路径。
- 夹爪配置分支在 `0x978fc` 和 `0x97920` 将上下界转为 float32；`0x97914/0x97924` 以 `fcmpe s8,s9/s0` 比较，仍仅小于/大于时拒绝。
- 当前 `studio/sdk_worker.py` 原样设置 `req.joint_positions=target`，没有精度改写；`studio/sdk_limits.py` 已使用相同的 float32 范围比较语义。

关键指令节选见 `sdk_float32_disassembly.txt`。反汇编仅用于核对安装库的数值判定，没有执行库函数。

## 纯 Python 数值复现

使用 `struct.pack/unpack('<f', ...)` 复现 IEEE754 float32 转换：

```text
配置上界 double:                  0.025
Parquet float32 升 double:       0.02500000037252903
SDK float32(请求值):             0.02500000037252903
SDK float32(配置上界):           0.02500000037252903
端点是否 > 上界:                 False
下一个更大的 float32:            0.02500000223517418
下一个 float32 是否 > 上界:       True
```

本结论针对上述 SDK 二进制内的范围判断，不把它当作控制权已取得、下层控制器已接受或实机已正确跟随的验证。当前未进行真机回放。本次不改数据、不改发送值；SDK 返回结果继续由现有回放日志记录。
