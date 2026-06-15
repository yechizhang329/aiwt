# 用户管理 SOP

## 当前用户列表

| 用户名 | 姓名 | 角色 | 邮箱 |
|---|---|---|---|
| admin | 管理员 | admin | admin@wangspecial.internal |
| assistant_a | 助理A | user | assistant_a@wangspecial.internal |

*每次增删用户后请更新此表。*

---

## 添加新用户

### 方法一：脚本自动写入（推荐）

```bash
cd webapp
python3 scripts/add_user.py \
  --username assistant_b \
  --name "助理B" \
  --email assistant_b@wangspecial.internal \
  --role user \
  --write
```

脚本会提示输入密码，自动生成 bcrypt hash 并写入 `config/users.yaml`。

```bash
# 写入后重启 Streamlit
# macOS: 在终端按 Ctrl+C 停止，然后重新运行
python3 -m streamlit run app.py
```

### 方法二：手动编辑

1. 生成密码 hash：
   ```bash
   python3 -c "import bcrypt, getpass; pw=getpass.getpass(); print(bcrypt.hashpw(pw.encode(), bcrypt.gensalt()).decode())"
   ```
2. 编辑 `config/users.yaml`，在 `credentials.usernames:` 下添加：
   ```yaml
   assistant_b:
     email: assistant_b@wangspecial.internal
     name: 助理B
     password: "$2b$12$..."   # 上一步生成的 hash
     role: user               # user 或 admin
   ```
3. 重启 Streamlit。

---

## 重置密码

```bash
cd webapp
python3 scripts/add_user.py \
  --username assistant_a \
  --name "助理A" \
  --email assistant_a@wangspecial.internal \
  --role user \
  --write
```

> 注意：`--write` 会提示用户名已存在并报错。此时改用手动编辑方法，只替换 `password` 字段。

**手动重置步骤**：
1. 生成新 hash（见上）
2. 在 `users.yaml` 中替换对应用户的 `password` 值
3. 重启 Streamlit

---

## 删除用户

1. 编辑 `config/users.yaml`，删除对应用户块
2. 重启 Streamlit

---

## 角色说明

| 角色 | 权限 |
|---|---|
| `user`（助理）| 只能查看自己提交的 case |
| `admin`（管理员）| 可查看所有人提交的 case |

---

## 部署注意事项

- `config/users.yaml` 包含 hashed 密码，已加入 `.gitignore`，不上传 git。
- Cookie 密钥必须通过环境变量设置，否则重启后所有 session 失效：
  ```bash
  export SCENE1_COOKIE_KEY="your-random-secret-32-chars-minimum"
  python3 -m streamlit run app.py
  ```
- 建议用 `python3 -c "import secrets; print(secrets.token_hex(32))"` 生成 key。

---

## Phase 2 升级触发条件

当以下任一条件成立时，升级到 SQLite users 表 + admin 管理界面：
- 用户数超过 5 人
- 人员变动频繁（每月 >2 次增删）
- 需要用户自助改密码

升级工程量约 1-2 小时。联系 @WebAppDev 执行。
