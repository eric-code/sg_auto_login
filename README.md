# 用途
省平台ukey用户自动登录

# 使用方法
在exe文件同目录修改名为config.json的文件，双击exe文件即可执行,可用windows任务计划程序进行定时调度。
第一次执行会在exe文件同目录生成certs文件夹，将ca.crt

## 如何在机器 B （插着 UKey 的那台）上正确配置

为了确保万无一失，请在**机器 B**上按以下步骤操作：

### 第一步：开启端口映射
用**管理员身份**运行 CMD（命令提示符），执行：

```cmd
netsh interface portproxy add v4tov4 listenport=21061 listenaddress=0.0.0.0 connectport=21061 connectaddress=127.0.0.1
```
*   **解释**：这句话的意思是“监听本机所有网卡（0.0.0.0）的 21061 端口，把收到的数据全部转发给本机的 127.0.0.1:21061”。
*   这样就绕过了驱动程序“只允许本机连接”的限制。

### 第二步：确保 IP Helper 服务已启动（关键！）
`netsh portproxy` 依赖于 Windows 的 `IP Helper` 服务。如果这个服务被禁用了，映射会失效。
在 CMD 中执行：
```cmd
sc config iphlpsvc start= auto
net start iphlpsvc
```
或powershell中执行：
```cmd
Set-Service -Name iphlpsvc -StartupType Automatic
net start iphlpsvc
```


### 第三步：放行防火墙（最容易忘的一步）
映射做好了，但如果 Windows 防火墙挡住了外部请求也是白搭。
在 CMD 中执行：
```cmd
netsh advfirewall firewall add rule name="Allow_UKey_Proxy" dir=in action=allow protocol=TCP localport=21061
```

### 第四步：验证是否成功
在 CMD 中执行：
```cmd
netsh interface portproxy show all
```
如果你看到刚才添加的规则，说明配置成功。

此时，在**机器 A** 上的 Python 脚本就可以连接 `wss://192.168.x.x:21061/xtxapp`（注意要配置 `ssl_context` 忽略证书错误）。