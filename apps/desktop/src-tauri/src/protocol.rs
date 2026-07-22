use serde::{Deserialize, Serialize};
use serde_json::Value;
use thiserror::Error;

pub const PROTOCOL_VERSION: u32 = 1;
pub const MAX_MESSAGE_BYTES: usize = 1024 * 1024;

#[derive(Debug, Clone, PartialEq, Eq, Deserialize, Serialize)]
pub struct RpcError {
    pub code: String,
    pub message: String,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub diagnostic_id: Option<String>,
}

#[derive(Debug, Clone, PartialEq)]
pub enum InboundMessage {
    Response {
        id: Option<String>,
        result: Option<Value>,
        error: Option<RpcError>,
    },
    Event {
        event: String,
        data: Value,
    },
}

#[derive(Debug, Error)]
pub enum ProtocolDecodeError {
    #[error("message exceeds the 1 MiB limit")]
    MessageTooLarge,
    #[error("invalid JSON: {0}")]
    InvalidJson(#[from] serde_json::Error),
    #[error("unsupported protocol version")]
    UnsupportedVersion,
    #[error("invalid {0} envelope")]
    InvalidEnvelope(&'static str),
}

#[derive(Deserialize)]
struct Envelope {
    v: u32,
    #[serde(rename = "type")]
    kind: String,
    #[serde(default)]
    id: Option<String>,
    #[serde(default)]
    result: Option<Value>,
    #[serde(default)]
    error: Option<RpcError>,
    #[serde(default)]
    event: Option<String>,
    #[serde(default)]
    data: Option<Value>,
}

pub fn decode_inbound(payload: &[u8]) -> Result<InboundMessage, ProtocolDecodeError> {
    if payload.len() > MAX_MESSAGE_BYTES {
        return Err(ProtocolDecodeError::MessageTooLarge);
    }
    let envelope: Envelope = serde_json::from_slice(payload)?;
    if envelope.v != PROTOCOL_VERSION {
        return Err(ProtocolDecodeError::UnsupportedVersion);
    }

    match envelope.kind.as_str() {
        "response" if envelope.result.is_some() || envelope.error.is_some() => {
            Ok(InboundMessage::Response {
                id: envelope.id,
                result: envelope.result,
                error: envelope.error,
            })
        }
        "event" => Ok(InboundMessage::Event {
            event: envelope
                .event
                .filter(|event| !event.is_empty())
                .ok_or(ProtocolDecodeError::InvalidEnvelope("event"))?,
            data: envelope.data.unwrap_or_else(|| Value::Object(Default::default())),
        }),
        "response" => Err(ProtocolDecodeError::InvalidEnvelope("response")),
        _ => Err(ProtocolDecodeError::InvalidEnvelope("message")),
    }
}
