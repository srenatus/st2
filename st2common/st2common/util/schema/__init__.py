import os
import copy

import six
import jsonschema
from jsonschema import _validators
from jsonschema.validators import create

from st2common.util import jsonify

__all__ = [
    'get_validator',
    'get_parameter_schema',
    'validate'
]

# https://github.com/json-schema/json-schema/blob/master/draft-04/schema
# The source material is licensed under the AFL or BSD license.
# Both draft 4 and custom schema has additionalProperties set to false by default.
# The custom schema differs from draft 4 with the extension of position, immutable,
# and draft 3 version of required.
PATH = os.path.join(os.path.dirname(os.path.realpath(__file__)))
SCHEMAS = {
    'draft4': jsonify.load_file('%s/draft4.json' % PATH),
    'custom': jsonify.load_file('%s/custom.json' % PATH)
}

SCHEMA_ANY_TYPE = {
    "anyOf": [
        {"type": "array"},
        {"type": "boolean"},
        {"type": "integer"},
        {"type": "number"},
        {"type": "object"},
        {"type": "string"}
    ]
}


def get_draft_schema(version='custom', additional_properties=False):
    schema = copy.deepcopy(SCHEMAS[version])
    if additional_properties and 'additionalProperties' in schema:
        del schema['additionalProperties']
    return schema


def extend_with_default(validator_class):
    validate_properties = validator_class.VALIDATORS["properties"]

    def set_defaults(validator, properties, instance, schema):
        for error in validate_properties(
            validator, properties, instance, schema,
        ):
            yield error

        for property, subschema in six.iteritems(properties):
            if "default" in subschema:
                instance.setdefault(property, subschema["default"])

    return jsonschema.validators.extend(
        validator_class, {"properties": set_defaults},
    )


CustomValidator = create(
    meta_schema=get_draft_schema(additional_properties=True),
    validators={
        u"$ref": _validators.ref,
        u"additionalItems": _validators.additionalItems,
        u"additionalProperties": _validators.additionalProperties,
        u"allOf": _validators.allOf_draft4,
        u"anyOf": _validators.anyOf_draft4,
        u"dependencies": _validators.dependencies,
        u"enum": _validators.enum,
        u"format": _validators.format,
        u"items": _validators.items,
        u"maxItems": _validators.maxItems,
        u"maxLength": _validators.maxLength,
        u"maxProperties": _validators.maxProperties_draft4,
        u"maximum": _validators.maximum,
        u"minItems": _validators.minItems,
        u"minLength": _validators.minLength,
        u"minProperties": _validators.minProperties_draft4,
        u"minimum": _validators.minimum,
        u"multipleOf": _validators.multipleOf,
        u"not": _validators.not_draft4,
        u"oneOf": _validators.oneOf_draft4,
        u"pattern": _validators.pattern,
        u"patternProperties": _validators.patternProperties,
        u"properties": _validators.properties_draft3,
        u"type": _validators.type_draft4,
        u"uniqueItems": _validators.uniqueItems,
    },
    version="action_param",
)


def validate(instance, schema, cls=None, use_default=True, *args, **kwargs):
    """
    Custom validate function which supports default arguments combined with the "required"
    property.

    :param use_default: True to support the use of the optional default property.
    :type use_default: ``bool``
    """
    instance = copy.deepcopy(instance)
    schema_type = schema.get('type', None)
    instance_is_dict = isinstance(instance, dict)

    if use_default and schema_type == 'object' and instance_is_dict:
        properties = schema.get('properties', {})
        for property_name, property_data in six.iteritems(properties):
            default_value = property_data.get('default', None)

            # Assign default value on the instance so the validation doesn't fail if requires is
            # true but the value is not provided
            if default_value is not None and getattr(instance, property_name, None) is None:
                instance[property_name] = default_value

    result = jsonschema.validate(instance=instance, schema=schema, cls=cls, *args, **kwargs)
    return result


VALIDATORS = {
    'draft4': jsonschema.Draft4Validator,
    'custom': CustomValidator
}


def get_validator(version='custom', assign_property_default=False):
    validator = VALIDATORS[version]
    return extend_with_default(validator) if assign_property_default else validator


def get_parameter_schema(model):
    # Dynamically construct JSON schema from the parameters metadata.
    def normalize(x):
        return {k: v if v else SCHEMA_ANY_TYPE for k, v in six.iteritems(x)}

    schema = {}
    from st2common.util.action_db import get_runnertype_by_name
    runner_type = get_runnertype_by_name(model.runner_type['name'])

    properties = normalize(runner_type.runner_parameters)
    properties.update(normalize(model.parameters))
    if properties:
        schema['title'] = model.name
        if model.description:
            schema['description'] = model.description
        schema['type'] = 'object'
        schema['properties'] = properties
        schema['additionalProperties'] = False
    return schema
