use appended_sensor_proto::config::{self, ProtobufMapping};
use appended_sensor_proto::generated_mapping::{
    update_proto_bool_generated, update_proto_field_generated,
};
use appended_sensor_proto::proto::orion::OrionSensorData;
use prost::Message;

fn main() {
    let packets = config::packets();
    let speed = packets.iter().find(|packet| packet.packet_id == 256).unwrap();
    let status = packets.iter().find(|packet| packet.packet_id == 257).unwrap();
    let speed_mapping = speed.bytes[0].protobuf.as_ref().unwrap();
    let samples = status.bytes[0].protobuf.as_ref().unwrap();
    let active = &status.bytes[1].bitfield_encoding.as_ref().unwrap()[0];
    let mut data = OrionSensorData {
        packet_id: 123,
        time: 456,
        ..Default::default()
    };
    update_proto_field_generated(&mut data, &speed_mapping.field_name, 12.5, speed_mapping);
    update_proto_field_generated(&mut data, &samples.field_name, 7.0, samples);
    let second = ProtobufMapping {
        field_index: Some(1),
        ..samples.clone()
    };
    update_proto_field_generated(&mut data, &second.field_name, 0.0, &second);
    update_proto_bool_generated(&mut data, &active.protobuf_field, true);
    assert!(data.controls.as_ref().unwrap().active);
    update_proto_bool_generated(&mut data, &active.protobuf_field, false);
    std::fs::write(std::env::args().nth(1).unwrap(), data.encode_to_vec()).unwrap();
    println!(
        "{}",
        serde_json::json!({
            "headers": config::CSV_HEADERS,
            "repeated_fields": config::REPEATED_FIELDS,
            "from": status.from,
            "quantity": status.quantity,
            "bit_index": active.bit_index,
        })
    );
}
