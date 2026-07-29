from a2a.types.a2a_pb2 import Message


def strip_provenance(message: Message) -> Message:
    """Returns a copy of message with metadata.provenance removed, if present."""
    tampered = Message()
    tampered.CopyFrom(message)
    if "provenance" in tampered.metadata.fields:
        del tampered.metadata.fields["provenance"]
    return tampered
