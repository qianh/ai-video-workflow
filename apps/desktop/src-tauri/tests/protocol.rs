use ai_video_workflow_desktop::protocol::{decode_inbound, InboundMessage, RpcError};
use serde_json::json;

#[test]
fn decodes_response_and_event_envelopes() {
    let response = decode_inbound(
        br#"{"v":1,"type":"response","id":"r1","result":{"ok":true}}"#,
    )
    .expect("response should decode");
    let event = decode_inbound(
        br#"{"v":1,"type":"event","event":"request.progress","data":{"current":1}}"#,
    )
    .expect("event should decode");

    assert_eq!(
        response,
        InboundMessage::Response {
            id: Some("r1".into()),
            result: Some(json!({"ok": true})),
            error: None,
        }
    );
    assert_eq!(
        event,
        InboundMessage::Event {
            event: "request.progress".into(),
            data: json!({"current": 1}),
        }
    );
}

#[test]
fn decodes_remote_error() {
    let message = decode_inbound(
        br#"{"v":1,"type":"response","id":"r2","error":{"code":"BAD_INPUT","message":"Nope","diagnostic_id":"d1"}}"#,
    )
    .expect("error response should decode");

    assert_eq!(
        message,
        InboundMessage::Response {
            id: Some("r2".into()),
            result: None,
            error: Some(RpcError {
                code: "BAD_INPUT".into(),
                message: "Nope".into(),
                diagnostic_id: Some("d1".into()),
            }),
        }
    );
}

#[test]
fn rejects_wrong_version_unknown_type_and_oversized_lines() {
    assert!(decode_inbound(br#"{"v":2,"type":"event","event":"x","data":{}}"#).is_err());
    assert!(decode_inbound(br#"{"v":1,"type":"mystery"}"#).is_err());
    assert!(decode_inbound(&vec![b'x'; 1024 * 1024 + 1]).is_err());
}
