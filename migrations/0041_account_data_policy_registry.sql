-- Immutable, server-published data authorization policies.

CREATE TABLE account_data_policy_versions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    policy_type TEXT NOT NULL,
    data_kind TEXT NOT NULL,
    policy_version INTEGER NOT NULL CHECK(policy_version >= 1),
    policy_json TEXT NOT NULL,
    policy_sha256 TEXT NOT NULL CHECK(length(policy_sha256)=64),
    status TEXT NOT NULL CHECK(status IN ('published','retired')),
    published_at TEXT NOT NULL,
    UNIQUE(policy_type,data_kind,policy_version),
    UNIQUE(policy_type,data_kind,policy_sha256)
);

INSERT INTO account_data_policy_versions
  (policy_type,data_kind,policy_version,policy_json,policy_sha256,status,published_at)
VALUES
  ('account_data','quotes',1,'{"body":"仅在用户明确选择的页面范围内处理对应数据；撤销后不再用于新的处理任务。","data_kind":"quotes","policy_type":"account_data","policy_version":1,"scope_schema":{"additional_properties":false,"allowed_pages":["today","discover","research","paper","portfolio","reports","trade"],"required":["pages"],"type":"object"},"title":"行情数据使用授权"}','25ee9883d3353d029e7c1d4ffc9088d7bbadad2ad3765eeb37e26a4f304893f3','published','2026-08-16T00:00:00+00:00'),
  ('account_data','research',1,'{"body":"仅在用户明确选择的页面范围内处理对应数据；撤销后不再用于新的处理任务。","data_kind":"research","policy_type":"account_data","policy_version":1,"scope_schema":{"additional_properties":false,"allowed_pages":["today","discover","research","reports","lab","ai"],"required":["pages"],"type":"object"},"title":"研究数据使用授权"}','efbb34e483743929588fee9b4eac56116e0430cdfc7a8dfe68c6cea07bbe1048','published','2026-08-16T00:00:00+00:00'),
  ('account_data','content',1,'{"body":"仅在用户明确选择的页面范围内处理对应数据；撤销后不再用于新的处理任务。","data_kind":"content","policy_type":"account_data","policy_version":1,"scope_schema":{"additional_properties":false,"allowed_pages":["account","research","paper","portfolio","reports","lab"],"required":["pages"],"type":"object"},"title":"我的内容索引授权"}','5c41e5cb967fa39b58f91834d623e3fa9724b423ca3fc11732ccacd2667b0da6','published','2026-08-16T00:00:00+00:00'),
  ('account_data','ai_memory',1,'{"body":"仅在用户明确选择的页面范围内处理对应数据；撤销后不再用于新的处理任务。","data_kind":"ai_memory","policy_type":"account_data","policy_version":1,"scope_schema":{"additional_properties":false,"allowed_pages":["account","research","ai"],"required":["pages"],"type":"object"},"title":"AI 可控记忆授权"}','d9b92922599d86e735826b40befdd33f2d3c11ca963fe87afff940bd482ba819','published','2026-08-16T00:00:00+00:00');
