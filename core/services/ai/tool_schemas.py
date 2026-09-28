from core.services.ai_tools.validators import MIN_AI_TOOL_YEAR


def _argument_json_schema(argument):
    type_name = argument.type
    optional = not argument.required

    if type_name == "integer":
        schema_type = "integer"
    elif type_name == "string":
        schema_type = "string"
    else:
        schema_type = "string"

    schema = {
        "description": argument.description,
        "type": [schema_type, "null"] if optional else schema_type,
    }

    if argument.enum:
        enum_values = list(argument.enum)
        if optional:
            enum_values.append(None)
        schema["enum"] = enum_values

    if argument.maximum is not None:
        if schema_type == "string":
            schema["maxLength"] = argument.maximum
        else:
            schema["maximum"] = argument.maximum

    if argument.name.endswith("year"):
        schema["minimum"] = MIN_AI_TOOL_YEAR
    elif argument.name.endswith("month"):
        schema["minimum"] = 1
        schema["maximum"] = 12
    elif argument.name == "limit":
        schema["minimum"] = 1

    return schema


def ai_tool_definition_to_openai_tool(definition):
    properties = {}
    required = []
    for argument in definition.schema.arguments:
        properties[argument.name] = _argument_json_schema(argument)
        required.append(argument.name)

    return {
        "type": "function",
        "name": definition.name,
        "description": definition.schema.description,
        "parameters": {
            "type": "object",
            "properties": properties,
            "required": required,
            "additionalProperties": False,
        },
        "strict": True,
    }


def ai_tool_definitions_to_openai_tools(definitions):
    return [
        ai_tool_definition_to_openai_tool(definition)
        for definition in definitions
    ]
