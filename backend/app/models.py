from sqlalchemy import Column, String, Text, Integer, DateTime
from app.database import Base
import datetime

class AgentModel(Base):
    __tablename__ = "agents"

    id = Column(String, primary_key=True, index=True)
    name = Column(String, nullable=False)
    role = Column(String, nullable=False)
    system_prompt = Column(Text, nullable=False)
    model = Column(String, default="qwen3.5:9b")
    tools = Column(String, default="[]")       # JSON encoded string array
    schedules = Column(String, default="{}")   # JSON encoded object
    guardrails = Column(String, default="{}")  # JSON encoded object

class MessageLogModel(Base):
    __tablename__ = "message_logs"

    id = Column(Integer, primary_key=True, autoincrement=True)
    workflow_id = Column(String, index=True, nullable=False)
    sender = Column(String, nullable=False)
    content = Column(Text, nullable=False)
    tokens_used = Column(Integer, default=0)
    timestamp = Column(DateTime, default=datetime.datetime.utcnow)