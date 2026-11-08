package main

import (
	orion "github.com/LonghornRacingElectric/lhre/apps/telemetry/stack/kafka/proto/orion"
	"google.golang.org/protobuf/reflect/protoreflect"
)

func orionSeedData() *orion.OrionSensorData {
	data := &orion.OrionSensorData{}
	message := data.ProtoReflect()
	fields := message.Descriptor().Fields()
	for index := 0; index < fields.Len(); index++ {
		field := fields.Get(index)
		if field.Kind() == protoreflect.MessageKind {
			message.Mutable(field)
		}
	}
	return data
}

func orionSchemaFields(message protoreflect.Message) map[string]interface{} {
	output := make(map[string]interface{})
	fields := message.Descriptor().Fields()
	for index := 0; index < fields.Len(); index++ {
		field := fields.Get(index)
		value := message.Get(field)
		if field.Kind() == protoreflect.MessageKind {
			if message.Has(field) {
				for name, child := range orionSchemaFields(value.Message()) {
					output[name] = child
				}
			}
		} else if field.IsList() {
			var values []interface{}
			list := value.List()
			for element := 0; element < list.Len(); element++ {
				values = append(values, list.Get(element).Interface())
			}
			output[string(field.Name())] = values
		} else {
			output[string(field.Name())] = value.Interface()
		}
	}
	return output
}
