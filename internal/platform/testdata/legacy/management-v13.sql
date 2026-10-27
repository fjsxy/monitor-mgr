-- PR31 v13 的原确认表；新版读取兼容不能清空这些独立于创建角色的记录。
CREATE TABLE instance_management_confirmations(sequence INTEGER PRIMARY KEY AUTOINCREMENT,instance_id TEXT NOT NULL REFERENCES instances(id),actor_id TEXT NOT NULL REFERENCES accounts(id),confirmed_at TEXT NOT NULL,allow_cluster_managers INTEGER NOT NULL CHECK(allow_cluster_managers IN (0,1)));
CREATE INDEX instance_management_latest ON instance_management_confirmations(instance_id,sequence DESC);
