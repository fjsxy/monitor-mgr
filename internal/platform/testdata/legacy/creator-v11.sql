-- PR31 v11 的原字段及约束；不从当前账号角色补造历史创建身份。
ALTER TABLE instances ADD COLUMN creator_user_id TEXT NOT NULL DEFAULT '';
ALTER TABLE instances ADD COLUMN creator_role TEXT NOT NULL DEFAULT '' CHECK(creator_role IN ('','ADMIN','USER'));
