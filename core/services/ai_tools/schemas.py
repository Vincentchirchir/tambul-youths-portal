from dataclasses import dataclass, field
from typing import Any, Callable


@dataclass(frozen=True)
class AIToolArgument:
    name: str
    type: str
    required: bool
    description: str
    enum: tuple[str, ...] = ()
    maximum: int | None = None

    def to_dict(self):
        data = {
            "name": self.name,
            "type": self.type,
            "required": self.required,
            "description": self.description,
        }
        if self.enum:
            data["enum"] = list(self.enum)
        if self.maximum is not None:
            data["maximum"] = self.maximum
        return data


@dataclass(frozen=True)
class AIToolSchema:
    name: str
    description: str
    arguments: tuple[AIToolArgument, ...]
    returns: dict[str, Any]
    sensitivity: str
    permission: str

    def to_dict(self):
        return {
            "name": self.name,
            "description": self.description,
            "arguments": [argument.to_dict() for argument in self.arguments],
            "returns": self.returns,
            "sensitivity": self.sensitivity,
            "permission": self.permission,
        }

    @property
    def argument_names(self):
        return {argument.name for argument in self.arguments}


@dataclass(frozen=True)
class AIToolDefinition:
    schema: AIToolSchema
    handler: Callable[[Any, dict[str, Any]], dict[str, Any]]
    write_capable: bool = field(default=False)

    @property
    def name(self):
        return self.schema.name

    @property
    def sensitivity(self):
        return self.schema.sensitivity
