"""Tests for chat_store — pure SQLite helpers (no Flask, no AI calls)."""
import chat_store
import pytest


@pytest.fixture()
def db(tmp_path):
    """A freshly-initialised database in a temporary directory."""
    path = tmp_path / "test_chat.db"
    chat_store.init_db(path)
    return path


# ---------------------------------------------------------------------------
# derive_title
# ---------------------------------------------------------------------------

class TestDeriveTitle:
    def test_short_text_unchanged(self):
        assert chat_store.derive_title("Hello there") == "Hello there"

    def test_truncates_long_text(self):
        long = "x" * 70
        result = chat_store.derive_title(long, max_len=60)
        assert len(result) == 60
        assert result.endswith("…")

    def test_empty_returns_default(self):
        assert chat_store.derive_title("") == "New conversation"
        assert chat_store.derive_title("   ") == "New conversation"

    def test_strips_whitespace(self):
        assert chat_store.derive_title("  hi  ") == "hi"

    def test_exactly_max_len_not_truncated(self):
        text = "a" * 60
        assert chat_store.derive_title(text, max_len=60) == text


# ---------------------------------------------------------------------------
# Conversation CRUD
# ---------------------------------------------------------------------------

class TestConversationCRUD:
    def test_create_returns_uuid(self, db):
        cid = chat_store.create_conversation(db, title="Test")
        assert len(cid) == 36  # UUID string

    def test_exists_after_create(self, db):
        cid = chat_store.create_conversation(db)
        assert chat_store.conversation_exists(db, cid)

    def test_missing_does_not_exist(self, db):
        assert not chat_store.conversation_exists(db, "not-a-real-id")

    def test_list_empty_initially(self, db):
        assert chat_store.list_conversations(db) == []

    def test_list_returns_newest_first(self, db):
        c1 = chat_store.create_conversation(db, title="First")
        chat_store.create_conversation(db, title="Second")
        # Touch c1 by appending a message so its updated_at is newest
        chat_store.append_messages(db, c1, [{"role": "user", "content": "ping"}])
        convs = chat_store.list_conversations(db)
        assert convs[0]["id"] == c1

    def test_delete_removes_conversation(self, db):
        cid = chat_store.create_conversation(db)
        chat_store.delete_conversation(db, cid)
        assert not chat_store.conversation_exists(db, cid)

    def test_delete_cascades_to_messages(self, db):
        cid = chat_store.create_conversation(db)
        chat_store.append_messages(db, cid, [{"role": "user", "content": "hi"}])
        chat_store.delete_conversation(db, cid)
        assert chat_store.get_messages(db, cid) == []

    def test_update_summary(self, db):
        cid = chat_store.create_conversation(db)
        chat_store.update_summary(db, cid, "We set up warm scenes.")
        convs = chat_store.list_conversations(db)
        assert convs[0]["summary"] == "We set up warm scenes."

    def test_update_title(self, db):
        cid = chat_store.create_conversation(db)
        chat_store.update_title(db, cid, "My title")
        convs = chat_store.list_conversations(db)
        assert convs[0]["title"] == "My title"


# ---------------------------------------------------------------------------
# Message CRUD
# ---------------------------------------------------------------------------

class TestMessageCRUD:
    def test_append_and_get(self, db):
        cid = chat_store.create_conversation(db)
        msgs = [
            {"role": "user", "content": "Hello"},
            {"role": "assistant", "content": "Hi there!"},
        ]
        chat_store.append_messages(db, cid, msgs)
        stored = chat_store.get_messages(db, cid)
        assert len(stored) == 2
        assert stored[0]["role"] == "user"
        assert stored[0]["content"] == "Hello"
        assert stored[1]["content"] == "Hi there!"

    def test_complex_content_round_trips(self, db):
        cid = chat_store.create_conversation(db)
        blocks = [
            {"type": "text", "text": "Setting lights"},
            {"type": "tool_use", "id": "tu_1", "name": "set_channel", "input": {"ch": 1, "v": 255}},
        ]
        chat_store.append_messages(db, cid, [{"role": "assistant", "content": blocks}])
        stored = chat_store.get_messages(db, cid)
        assert stored[0]["content"] == blocks

    def test_message_count(self, db):
        cid = chat_store.create_conversation(db)
        assert chat_store.message_count(db, cid) == 0
        chat_store.append_messages(db, cid, [
            {"role": "user", "content": "a"},
            {"role": "assistant", "content": "b"},
        ])
        assert chat_store.message_count(db, cid) == 2

    def test_append_empty_list_is_noop(self, db):
        cid = chat_store.create_conversation(db)
        chat_store.append_messages(db, cid, [])
        assert chat_store.message_count(db, cid) == 0

    def test_messages_ordered_by_insertion(self, db):
        cid = chat_store.create_conversation(db)
        for i in range(5):
            chat_store.append_messages(db, cid, [{"role": "user", "content": str(i)}])
        stored = chat_store.get_messages(db, cid)
        assert [m["content"] for m in stored] == ["0", "1", "2", "3", "4"]


# ---------------------------------------------------------------------------
# Fork
# ---------------------------------------------------------------------------

class TestFork:
    def test_fork_copies_all_messages(self, db):
        cid = chat_store.create_conversation(db, title="Original")
        chat_store.append_messages(db, cid, [
            {"role": "user", "content": "msg1"},
            {"role": "assistant", "content": "msg2"},
        ])
        new_id = chat_store.fork_conversation(db, cid)
        assert new_id != cid
        forked = chat_store.get_messages(db, new_id)
        assert len(forked) == 2

    def test_fork_with_upto_index(self, db):
        cid = chat_store.create_conversation(db)
        chat_store.append_messages(db, cid, [
            {"role": "user", "content": "a"},
            {"role": "assistant", "content": "b"},
            {"role": "user", "content": "c"},
        ])
        # Fork only up to (not including) index 2
        new_id = chat_store.fork_conversation(db, cid, upto_index=2)
        forked = chat_store.get_messages(db, new_id)
        assert len(forked) == 2

    def test_fork_title_gets_suffix(self, db):
        cid = chat_store.create_conversation(db, title="My chat")
        new_id = chat_store.fork_conversation(db, cid)
        convs = {c["id"]: c for c in chat_store.list_conversations(db)}
        assert convs[new_id]["title"] == "My chat (fork)"

    def test_fork_empty_conversation(self, db):
        cid = chat_store.create_conversation(db)
        new_id = chat_store.fork_conversation(db, cid)
        assert chat_store.message_count(db, new_id) == 0


# ---------------------------------------------------------------------------
# Search
# ---------------------------------------------------------------------------

class TestSearch:
    def test_empty_query_returns_empty(self, db):
        assert chat_store.search_conversations(db, "") == []
        assert chat_store.search_conversations(db, "   ") == []

    def test_finds_matching_message(self, db):
        cid = chat_store.create_conversation(db, title="Warm session")
        chat_store.append_messages(db, cid, [
            {"role": "user", "content": "Set up warm amber lighting"},
            {"role": "assistant", "content": "Done, set to warm amber."},
        ])
        results = chat_store.search_conversations(db, "warm")
        assert len(results) >= 1
        assert any(r["conversation_id"] == cid for r in results)

    def test_no_match_returns_empty(self, db):
        cid = chat_store.create_conversation(db, title="Blue session")
        chat_store.append_messages(db, cid, [
            {"role": "user", "content": "Set up cool blue lighting"},
        ])
        results = chat_store.search_conversations(db, "volcanic")
        assert results == []

    def test_results_grouped_by_conversation(self, db):
        cid = chat_store.create_conversation(db, title="Warm session")
        chat_store.append_messages(db, cid, [
            {"role": "user", "content": "warm lighting one"},
            {"role": "assistant", "content": "warm done one"},
        ])
        results = chat_store.search_conversations(db, "warm")
        # Both hits belong to the same conversation — should be grouped
        assert len(results) == 1
        assert len(results[0]["snippets"]) >= 1

    def test_search_across_multiple_conversations(self, db):
        c1 = chat_store.create_conversation(db, title="Session A")
        c2 = chat_store.create_conversation(db, title="Session B")
        chat_store.append_messages(db, c1, [{"role": "user", "content": "sunrise warm"}])
        chat_store.append_messages(db, c2, [{"role": "user", "content": "sunset warm"}])
        results = chat_store.search_conversations(db, "warm")
        found_ids = {r["conversation_id"] for r in results}
        assert c1 in found_ids
        assert c2 in found_ids
