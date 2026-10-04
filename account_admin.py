"""仅服务器终端可用的管理员授权/密码恢复；不通过公开网页自动创建管理员。"""
import argparse
from getpass import getpass
from dotenv import load_dotenv
from account_store import AccountStore, AccountError
from session_store import SessionStore, BASE_DIR


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action',choices=['promote','reset-password'])
    parser.add_argument('username',help='已注册账号的用户名或完整手机号，如 +8613800000000')
    parser.add_argument('--data-dir')
    args=parser.parse_args();load_dotenv(BASE_DIR/'.env')
    store=SessionStore(args.data_dir);accounts=AccountStore(store)
    with store.db() as db:
        row=db.execute('SELECT * FROM accounts WHERE username=? OR phone=?',(args.username.lower(),args.username)).fetchone()
    if not row:raise SystemExit('未找到账号，请先在网页注册')
    if args.action=='promote':
        with store.db() as db:db.execute("UPDATE accounts SET role='admin',enabled=1 WHERE user_id=?",(row['user_id'],))
        print('已授予管理员权限；刷新页面后可进入用户管理。')
    else:
        password=getpass('新密码（至少 6 个字符）：')
        if password!=getpass('再次输入：'):raise SystemExit('两次密码不一致，未修改')
        try:accounts.change_password(row['user_id'],password,verified=True)
        except AccountError as exc:raise SystemExit(str(exc))
        print('密码已重设，旧登录会话已撤销。')

if __name__=='__main__':main()
