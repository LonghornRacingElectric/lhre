package main

import (
	artemis "github.com/LonghornRacingElectric/lhre/apps/telemetry/stack/kafka/proto/artemis"
	"google.golang.org/protobuf/reflect/protoreflect"
)

func artemisSeedData() *artemis.ArtemisSensorData {
	data := &artemis.ArtemisSensorData{}
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

func schemaFields(message protoreflect.Message) map[string]interface{} {
	output := make(map[string]interface{})
	fields := message.Descriptor().Fields()
	for index := 0; index < fields.Len(); index++ {
		field := fields.Get(index)
		value := message.Get(field)
		if field.Kind() == protoreflect.MessageKind {
			if message.Has(field) {
				for name, child := range schemaFields(value.Message()) {
					output[name] = child
				}
			}
		} else if field.IsList() {
			values := []interface{}{}
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
