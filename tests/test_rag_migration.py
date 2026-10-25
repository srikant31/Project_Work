"""The additive RAG metadata upgrade preserves existing conversation rows."""

from pathlib import Path

import pytest


@pytest.mark.integration
def test_rag_metadata_migration_is_repeatable_and_preserves_existing_content(db_factory):
    from sqlalchemy import text
    from backend.database.models import Conversation, Message, User

    with db_factory() as session:
        user = User(email="migration-rag@example.test", role="employee", password_hash="preserve-existing-hash")
        session.add(user)
        session.flush()
        conversation = Conversation(user_id=user.user_id, title="Existing history")
        session.add(conversation)
        session.flush()
        message = Message(conversation_id=conversation.id, message_provider="user", content="Original saved question")
        session.add(message)
        session.commit()
        user_id, message_id, conversation_id = user.user_id, message.message_id, conversation.id
    engine = db_factory.kw["bind"]
    with engine.begin() as connection:
        connection.execute(text("ALTER TABLE messages DROP COLUMN generation_metadata"))
    sql = (Path(__file__).resolve().parents[1] / "backend/database/migrations/005_rag_prototype.sql").read_text()
    with engine.connect().execution_options(isolation_level="AUTOCOMMIT") as connection:
        connection.exec_driver_sql(sql)
    with db_factory() as session:
        row = session.get(Message, message_id)
        assert row.content == "Original saved question" and row.conversation_id == conversation_id
        assert row.generation_metadata is None
        row.generation_metadata = {"test_migration_note": "preserve-added-metadata"}
        session.commit()
    with engine.connect().execution_options(isolation_level="AUTOCOMMIT") as connection:
        connection.exec_driver_sql(sql)
    with db_factory() as session:
        assert session.get(User, user_id).password_hash == "preserve-existing-hash"
        assert session.get(Conversation, conversation_id).title == "Existing history"
        row = session.get(Message, message_id)
        assert row.content == "Original saved question"
        assert row.generation_metadata == {"test_migration_note": "preserve-added-metadata"}
