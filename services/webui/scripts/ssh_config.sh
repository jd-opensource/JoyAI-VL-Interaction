#!/bin/bash
 
# 检查是否为root用户
if [ "$(id -u)" -ne 0 ]; then
    echo "请使用root用户或sudo权限运行此脚本。"
    exit 1
fi
 
# 检查sshd服务是否运行
# if pgrep -x "sshd" &>/dev/null; then
#     echo "sshd服务已经运行。无需重复配置。"
#     exit 0
# fi
 
# 检查netstat命令是否存在
if command -v netstat &>/dev/null; then
    # 检查2345端口是否被占用
    if netstat -tuln | grep -q ":2345"; then
        echo "端口2345已被占用，请释放该端口或使用其他端口。"
        exit 1
    fi
else
    echo "netstat命令不存在，跳过端口检查。"
fi
 
# 检查sshd服务是否安装
if ! command -v sshd &>/dev/null; then
    echo "sshd 服务未安装，正在安装..."
    if command -v apt &>/dev/null; then
        apt update && apt install -y openssh-server
    elif command -v yum &>/dev/null; then
        yum install -y openssh-server
    else
        echo "无法检测到合适的包管理器，请手动安装sshd服务。"
        exit 1
    fi
    echo "sshd 服务安装完成。"
else
    echo "sshd 服务已安装。"
fi
 
# 配置sshd服务
echo "配置sshd服务..."
SSHD_CONFIG="/etc/ssh/sshd_config"
 
# 确保目录存在
mkdir -p /var/run/sshd
 
# 修改配置以允许root登录
if grep -q "^PermitRootLogin" "$SSHD_CONFIG"; then
    sed -i "s/^PermitRootLogin.*/PermitRootLogin yes/" "$SSHD_CONFIG"
else
    echo "PermitRootLogin yes" >> "$SSHD_CONFIG"
fi
 
# 修改端口号为2345
if grep -q "^#Port" "$SSHD_CONFIG"; then
    sed -i "s/^#Port.*/Port 2345/" "$SSHD_CONFIG"
elif grep -q "^Port" "$SSHD_CONFIG"; then
    sed -i "s/^Port.*/Port 2345/" "$SSHD_CONFIG"
else
    echo "Port 2345" >> "$SSHD_CONFIG"
fi
 
# 禁用UsePAM
if grep -q "^#UsePAM" "$SSHD_CONFIG"; then
    sed -i "s/^#UsePAM.*/UsePAM no/" "$SSHD_CONFIG"
elif grep -q "^UsePAM" "$SSHD_CONFIG"; then
    sed -i "s/^UsePAM.*/UsePAM no/" "$SSHD_CONFIG"
else
    echo "UsePAM no" >> "$SSHD_CONFIG"
fi
 
# 设置root密码
echo "设置root用户密码..."
echo "root:123******test" | 123******test
 
# 启动sshd服务
echo "启动sshd服务..."
/usr/sbin/sshd -D &
echo "sshd服务已启动。"
 
# 获取容器IP地址
CONTAINER_IP=$(hostname -I | awk '{print $1}')
 
# 提示用户SSH命令
if [ -n "$CONTAINER_IP" ]; then
    echo "设置完成！"
    echo "- SSH服务已启动并监听端口 2345。"
    echo "- Root用户可以通过以下命令登录："
    echo "  ssh -p 2345 root@$CONTAINER_IP"
    echo "  默认密码已设置为123******test，可在脚本中更改"
else
    echo "设置完成！但未能获取容器的IP地址，请检查网络配置。"
fi
 
