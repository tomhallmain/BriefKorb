"""
Messages service helpers for BriefKorb's web interface.

MessagesService (the Microsoft-only, hand-rolled-Graph-calls class this
file used to hold) was removed once django_app/messages/views.py's
messages_view/messages_api_view/inbox_view all migrated onto
UnifiedEmailServer (email_server/__init__.py) -- confirmed via a full-repo
grep that nothing else referenced it. annotate_sender_impact is the one
piece that survived the migration: it's provider-agnostic (operates on
already-aggregated sender-bucket dicts, not raw provider messages), so it's
shared by every one of those views regardless of which one does the
aggregating.
"""

from typing import Any, Dict, List
from pathlib import Path
import sys

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from email_client.utils.sender_categorization import SenderCategorizationManager


def annotate_sender_impact(message_data: List[Dict[str, Any]], sender_categorization: SenderCategorizationManager) -> List[Dict[str, Any]]:
    """Infer and annotate sender impact for message groups (in place, and
    returning the same list for convenient chaining).

    Free function rather than a class method so any view can call it with
    its own SenderCategorizationManager instance, regardless of which
    provider(s) or aggregation path produced message_data.
    """
    for message_info in message_data:
        sender_address = (message_info.get('fromAddress') or '').strip().lower()
        if not sender_address:
            continue
        subject = message_info.get('subject') or ''
        inference = sender_categorization.infer_for_sender(sender_address, [subject])
        sender_categorization.set_inferred_sender_impact(sender_address, inference)
        message_info['impact'] = sender_categorization.get_sender_impact(sender_address).value
        message_info['genericInferenceScore'] = inference.generic_inference_score
        message_info['blocklistInferenceScore'] = inference.blocklist_inference_score
        message_info['botSpamInferenceScore'] = inference.bot_spam_inference_score
        message_info['hasImpactException'] = sender_categorization.has_sender_exception(sender_address)
    return message_data
