-- ============================================================================
-- Tomori (#141 公共照明故障工单系统) — 演示种子数据（seed.sql）
-- ----------------------------------------------------------------------------
-- 与 `src/backend/tomori/services/auth.py:seed_demo_data()` 口径一致的纯 SQL 版
-- （供 `schema.sql` 直建库后一次性导入；所有写入均幂等 `ON CONFLICT DO NOTHING`）。
--
-- 内容：3 角色 / 3 运维区域（1 市 + 2 片区）/ 4 演示账号（ADMIN×1 INSPECTOR×1
-- TECHNICIAN×2）/ 6 条范围授权 / 5 条业务规则（与迁移 `0004` 相同 JSONB）/
-- 3 照明点位 / 3 设施资产 / 2 张示例工单（巡查员人工 + AI 候选）。
--
-- 演示口令：所有账号共享 argon2id 哈希，明文 `demo-2026-tomori`（仅限本地演示，
-- 生产环境必须替换）。令牌/密钥类敏感值一律写 `__PLACEHOLDER__`，不写真值。
-- 使用：psql -h 127.0.0.1 -p 25432 -U tomori -d <db> -f src/database/seed.sql
-- ============================================================================

BEGIN;

-- 1. roles（迁移 0001 bulk_insert 的同构等价，供 schema.sql 直建库使用）
INSERT INTO roles (code, name, description) VALUES
  ('ADMIN',      '系统运维管理员', '全域配置/工单与派单管理'),
  ('INSPECTOR',  '巡查员',         '现场巡查上报与 AI 候选确认'),
  ('TECHNICIAN', '维修员',         '接单维修与验收回执')
ON CONFLICT (code) DO NOTHING;

-- 2. maintenance_regions（3 区域：1 市级 + 2 维护片区）
INSERT INTO maintenance_regions (region_code, name, parent_region_id, level, description, is_active) VALUES
  ('ZZ-HQ-01', '郑州市城市照明运维中心', NULL, 1, '市级总管辖区', true)
ON CONFLICT (region_code) DO NOTHING;

INSERT INTO maintenance_regions (region_code, name, parent_region_id, level, description, is_active) VALUES
  ('ZZ-JS-01', '金水区维护片区', (SELECT id FROM maintenance_regions WHERE region_code='ZZ-HQ-01'), 2, '金水区核心照明维护区域', true),
  ('ZZ-ZD-01', '郑东新区维护片区', (SELECT id FROM maintenance_regions WHERE region_code='ZZ-HQ-01'), 2, '郑东新区公共照明维护区域', true)
ON CONFLICT (region_code) DO NOTHING;

-- 3. accounts（演示口令 `demo-2026-tomori`，argon2id 哈希；生产环境必须替换为 __PLACEHOLDER__）
INSERT INTO accounts (username, password_hash, full_name, employee_no, status, skills, max_active_dispatch, token_version) VALUES
  ('admin',      '$argon2id$v=19$m=65536,t=3,p=4$WsAWhV9WlmcDwKE39xkjjw$FrBQfBM4tFlP6+J99izFIiAf8RsfpLRg69YMNr2iosM', '系统运维管理员', 'EMP-0001', 'ACTIVE', '{}'::text[], 3, 1),
  ('inspector1', '$argon2id$v=19$m=65536,t=3,p=4$WsAWhV9WlmcDwKE39xkjjw$FrBQfBM4tFlP6+J99izFIiAf8RsfpLRg69YMNr2iosM', '巡查员李明',     'EMP-0002', 'ACTIVE', '{}'::text[], 3, 1),
  ('tech1',      '$argon2id$v=19$m=65536,t=3,p=4$WsAWhV9WlmcDwKE39xkjjw$FrBQfBM4tFlP6+J99izFIiAf8RsfpLRg69YMNr2iosM', '维修员张伟',     'EMP-0003', 'ACTIVE', '{LAMP_OUT,FIXTURE_DAMAGED,ELECTRIC_HAZARD}'::text[], 3, 1),
  ('tech2',      '$argon2id$v=19$m=65536,t=3,p=4$WsAWhV9WlmcDwKE39xkjjw$FrBQfBM4tFlP6+J99izFIiAf8RsfpLRg69YMNr2iosM', '维修员王强',     'EMP-0004', 'ACTIVE', '{OCCLUDED,FLICKERING,LAMP_OUT}'::text[], 3, 1)
ON CONFLICT (username) DO NOTHING;

-- 4. account_roles
INSERT INTO account_roles (account_id, role_id) VALUES
  ((SELECT id FROM accounts WHERE username='admin'),      (SELECT id FROM roles WHERE code='ADMIN')),
  ((SELECT id FROM accounts WHERE username='inspector1'),(SELECT id FROM roles WHERE code='INSPECTOR')),
  ((SELECT id FROM accounts WHERE username='tech1'),     (SELECT id FROM roles WHERE code='TECHNICIAN')),
  ((SELECT id FROM accounts WHERE username='tech2'),     (SELECT id FROM roles WHERE code='TECHNICIAN'))
ON CONFLICT DO NOTHING;

-- 5. scope_authorizations（6 条：与 seed_demo_data() 一致）
INSERT INTO scope_authorizations (account_id, region_id, scope_type, is_active, granted_by_account_id, remark) VALUES
  ((SELECT id FROM accounts WHERE username='inspector1'), (SELECT id FROM maintenance_regions WHERE region_code='ZZ-JS-01'), 'INSPECT', true, (SELECT id FROM accounts WHERE username='admin'), 'Initial demo bootstrap'),
  ((SELECT id FROM accounts WHERE username='tech1'),      (SELECT id FROM maintenance_regions WHERE region_code='ZZ-JS-01'), 'REPAIR',  true, (SELECT id FROM accounts WHERE username='admin'), 'Initial demo bootstrap'),
  ((SELECT id FROM accounts WHERE username='tech2'),      (SELECT id FROM maintenance_regions WHERE region_code='ZZ-JS-01'), 'REPAIR',  true, (SELECT id FROM accounts WHERE username='admin'), 'Initial demo bootstrap'),
  ((SELECT id FROM accounts WHERE username='tech2'),      (SELECT id FROM maintenance_regions WHERE region_code='ZZ-ZD-01'), 'REPAIR',  true, (SELECT id FROM accounts WHERE username='admin'), 'Initial demo bootstrap'),
  ((SELECT id FROM accounts WHERE username='admin'),      (SELECT id FROM maintenance_regions WHERE region_code='ZZ-JS-01'), 'MANAGE',  true, (SELECT id FROM accounts WHERE username='admin'), 'Initial demo bootstrap'),
  ((SELECT id FROM accounts WHERE username='admin'),      (SELECT id FROM maintenance_regions WHERE region_code='ZZ-ZD-01'), 'MANAGE',  true, (SELECT id FROM accounts WHERE username='admin'), 'Initial demo bootstrap');

-- 6. business_rules（与迁移 0004 _SEED_RULES 完全相同 JSONB，version=1, is_active=true）
INSERT INTO business_rules (rule_code, version, rule_value, description, is_active) VALUES
  ('grading_rules', 1,
   '{"rules":[{"any":[{"fault_type":"ELECTRIC_HAZARD"},{"safety_flag":true},{"road_level":"EXPRESS","asset_importance_in":["CRITICAL","HIGH"]}],"rule_id":"R1_SAFETY_OR_EXPRESS_CRITICAL","priority":"P1"},{"any":[{"road_level":"EXPRESS"},{"road_level":"MAIN","fault_type_in":["FIXTURE_DAMAGED","LAMP_OUT"]},{"asset_importance_in":["CRITICAL","HIGH"]}],"rule_id":"R2_EXPRESS_OR_MAIN_CORE_OR_CRITICAL_ASSET","priority":"P2"},{"any":[{"road_level":"BRANCH","fault_type_in":["LAMP_OUT","OCCLUDED","FLICKERING","FIXTURE_DAMAGED"]},{"road_level":"MAIN","fault_type_in":["OCCLUDED","FLICKERING","OTHER"]}],"rule_id":"R3_BRANCH_OR_MAIN_MINOR","priority":"P3"}],"display_names":{"P1":"特急","P2":"加急","P3":"普通","P4":"观察"},"default_rule_id":"R4_FALLBACK_OBSERVATION","default_priority":"P4"}'::jsonb,
   '表驱动确定性故障分级规则（P1 特急 / P2 加急 / P3 普通 / P4 观察，D-12/D-13）', true),
  ('sla_policy', 1,
   '{"response_minutes":null,"applies_to_priorities":["P1","P2","P3","P4"],"emergency_notice_hours":24,"statutory_deadline_hours":48}'::jsonb,
   '郑州市令第 152 号第 19 条第 3 款法定 48 小时维修养护/督促基线与第 24 条第 2 款 24h 紧急手续标志（D-11）', true),
  ('dispatch_scoring_weights', 1,
   '{"weights":{"workload":0.4,"skill":0.35,"on_time_rate":0.25},"max_recommendations":10,"overload_penalty_factor":0.0}'::jsonb,
   '区域派单候选维修员推荐排序权重（纯辅助参考排序，不影响法定 48h 时限）', true),
  ('rework_policy', 1,
   '{"on_exceed_action":"FORCE_ADMIN_REVIEW","max_rework_rounds":2}'::jsonb,
   '返修阈值与超阈值强制人工复核策略（最大正常返修 2 轮，D-14）', true),
  ('ai_candidate_ttl_hours', 1,
   '{"ttl_hours":72}'::jsonb,
   'AI 候选单未确认自动过期回收时限（72 小时，D-15）', true)
ON CONFLICT DO NOTHING;

-- 7. lighting_points（3 点位，与 seed_demo_data() 一致）
INSERT INTO lighting_points (point_code, region_id, name, road_name, road_level, longitude, latitude, lamp_count, power_supply_type, status) VALUES
  ('SL-ZZ-JS-0001', (SELECT id FROM maintenance_regions WHERE region_code='ZZ-JS-01'), '花园路北三环交叉口东 150m 第3杆', '花园路',   'MAIN',     113.6812340, 34.7891230, 2, 'GRID', 'IN_SERVICE'),
  ('SL-ZZ-JS-0002', (SELECT id FROM maintenance_regions WHERE region_code='ZZ-JS-01'), '中州大道金水立交北侧第8杆',     '中州大道', 'EXPRESS',  113.7112340, 34.7791230, 2, 'GRID', 'IN_SERVICE'),
  ('SL-ZZ-ZD-0001', (SELECT id FROM maintenance_regions WHERE region_code='ZZ-ZD-01'), '商务内环路如意湖东侧支路第5杆', '商务内环路', 'BRANCH',  113.7312340, 34.7721230, 2, 'GRID', 'IN_SERVICE')
ON CONFLICT (point_code) DO NOTHING;

-- 8. facility_assets（3 资产）
INSERT INTO facility_assets (asset_code, lighting_point_id, asset_type, name, rated_power_w, importance, status) VALUES
  ('AST-ZZ-JS-0001-L1', (SELECT id FROM lighting_points WHERE point_code='SL-ZZ-JS-0001'), 'LUMINAIRE',   '150W LED 主路灯具',   150.00, 'HIGH',     'IN_SERVICE'),
  ('AST-ZZ-JS-0002-C1', (SELECT id FROM lighting_points WHERE point_code='SL-ZZ-JS-0002'), 'CONTROL_BOX', '快速路配电箱 A1',     150.00, 'CRITICAL', 'IN_SERVICE'),
  ('AST-ZZ-ZD-0001-L1', (SELECT id FROM lighting_points WHERE point_code='SL-ZZ-ZD-0001'), 'LUMINAIRE',   '80W LED 景观支路灯具', 80.00,  'NORMAL',   'IN_SERVICE')
ON CONFLICT (asset_code) DO NOTHING;

-- 9. fault_tickets（2 张示例工单）
-- 9a. 巡查员人工上报已确认工单（PENDING_DISPATCH / P2）
INSERT INTO fault_tickets
  (ticket_no, region_id, lighting_point_id, facility_asset_id, report_source, fault_type,
   title, description, status, priority, grading_score,
   reporter_account_id, confirmed_by_inspector, confirmed_by_inspector_id, confirmed_at,
   response_deadline_at, repair_deadline_at)
VALUES
  ('FT-2026-0001',
   (SELECT id FROM maintenance_regions WHERE region_code='ZZ-JS-01'),
   (SELECT id FROM lighting_points WHERE point_code='SL-ZZ-JS-0001'),
   (SELECT id FROM facility_assets WHERE asset_code='AST-ZZ-JS-0001-L1'),
   'MANUAL_INSPECT', 'LAMP_OUT',
   '花园路杆位灭灯（夜间整排不亮）', '巡查员夜巡发现该杆位双灯全灭，怀疑灯头烧损。', 'PENDING_DISPATCH', 'P2', 65.00,
   (SELECT id FROM accounts WHERE username='inspector1'), true,
   (SELECT id FROM accounts WHERE username='inspector1'), now() - interval '2 hours',
   now() + interval '4 hours', now() + interval '48 hours')
ON CONFLICT (ticket_no) DO NOTHING;

-- 9b. AI 视觉识别候选单（AI_CANDIDATE_PENDING，待巡查员确认）
INSERT INTO fault_tickets
  (ticket_no, region_id, lighting_point_id, facility_asset_id, report_source, fault_type,
   title, description, status, priority,
   reporter_account_id, confirmed_by_inspector,
   ai_suggestion_json, ai_max_confidence,
   response_deadline_at, repair_deadline_at)
VALUES
  ('FT-2026-0002',
   (SELECT id FROM maintenance_regions WHERE region_code='ZZ-JS-01'),
   (SELECT id FROM lighting_points WHERE point_code='SL-ZZ-JS-0002'),
   (SELECT id FROM facility_assets WHERE asset_code='AST-ZZ-JS-0002-C1'),
   'AI_CANDIDATE', 'LAMP_OUT',
   'AI 巡查照片识别：中州大道杆位疑似灯具损坏', 'YOLO26s 夜间样图识别置信度 0.87，待巡查员现场确认。', 'AI_CANDIDATE_PENDING', NULL,
   (SELECT id FROM accounts WHERE username='inspector1'), false,
   '{"detected_classes":["streetlight_damaged"],"fusion":"consistent"}'::jsonb, 0.870,
   now() + interval '4 hours', now() + interval '48 hours')
ON CONFLICT (ticket_no) DO NOTHING;

COMMIT;

-- 序列校准：自增 id 已按现值推进，避免手工 INSERT id 后重复
SELECT setval(pg_get_serial_sequence('roles', 'id'), (SELECT COALESCE(MAX(id),1) FROM roles));
SELECT setval(pg_get_serial_sequence('maintenance_regions', 'id'), (SELECT COALESCE(MAX(id),1) FROM maintenance_regions));
SELECT setval(pg_get_serial_sequence('accounts', 'id'), (SELECT COALESCE(MAX(id),1) FROM accounts));
SELECT setval(pg_get_serial_sequence('account_roles', 'id'), (SELECT COALESCE(MAX(id),1) FROM account_roles));
SELECT setval(pg_get_serial_sequence('scope_authorizations', 'id'), (SELECT COALESCE(MAX(id),1) FROM scope_authorizations));
SELECT setval(pg_get_serial_sequence('business_rules', 'id'), (SELECT COALESCE(MAX(id),1) FROM business_rules));
SELECT setval(pg_get_serial_sequence('lighting_points', 'id'), (SELECT COALESCE(MAX(id),1) FROM lighting_points));
SELECT setval(pg_get_serial_sequence('facility_assets', 'id'), (SELECT COALESCE(MAX(id),1) FROM facility_assets));
SELECT setval(pg_get_serial_sequence('fault_tickets', 'id'), (SELECT COALESCE(MAX(id),1) FROM fault_tickets));
