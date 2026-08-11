-- Add starred and pinned columns to knowledge
ALTER TABLE knowledge ADD COLUMN IF NOT EXISTS starred BOOLEAN DEFAULT FALSE;
ALTER TABLE knowledge ADD COLUMN IF NOT EXISTS pinned BOOLEAN DEFAULT FALSE;

CREATE INDEX IF NOT EXISTS idx_knowledge_starred ON knowledge(user_id, starred) WHERE starred = TRUE;
CREATE INDEX IF NOT EXISTS idx_knowledge_pinned ON knowledge(user_id, pinned) WHERE pinned = TRUE;

-- Agents table for node registration
CREATE TABLE IF NOT EXISTS agents (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    name VARCHAR(255) NOT NULL,
    agent_type VARCHAR(50) DEFAULT 'generic',
    capabilities JSONB DEFAULT '[]',
    metadata JSONB DEFAULT '{}',
    token VARCHAR(255) NOT NULL,
    status VARCHAR(20) DEFAULT 'active',
    last_heartbeat TIMESTAMPTZ,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_agents_status ON agents(status);

-- Agent reports table
CREATE TABLE IF NOT EXISTS agent_reports (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    agent_id UUID NOT NULL REFERENCES agents(id) ON DELETE CASCADE,
    report_type VARCHAR(100) NOT NULL,
    data JSONB DEFAULT '{}',
    created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_agent_reports_agent_id ON agent_reports(agent_id);
