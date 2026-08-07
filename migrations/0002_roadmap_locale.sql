UPDATE roadmap_items
SET name='策略分享功能（規劃中）',
    description='用戶可分享、收藏策略；待社群規模與審核機制成熟後開放。',
    updated_at=datetime('now')
WHERE name='策略分享功能（规划中）';

INSERT INTO roadmap_items (quarter,name,status,sort_order,description,updated_at,created_at)
SELECT '待定','策略分享功能（規劃中）','planning',900,
       '用戶可分享、收藏策略；待社群規模與審核機制成熟後開放。',datetime('now'),datetime('now')
WHERE NOT EXISTS (SELECT 1 FROM roadmap_items WHERE name='策略分享功能（規劃中）');
