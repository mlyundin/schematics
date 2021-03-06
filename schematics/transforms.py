# -*- coding: utf-8 -*-

import collections
import itertools
import operator

from six import iteritems

from .common import *
from .datastructures import OrderedDict, Context
from .exceptions import *
from .types.compound import ModelType
from .undefined import Undefined
from .util import listify

try:
    basestring #PY2
except NameError:
    basestring = str #PY3

try:
    unicode #PY2
except:
    import codecs
    unicode = str #PY3



###
# Transform loops
###


def import_loop(cls, instance_or_dict, field_converter=None, trusted_data=None,
                mapping=None, partial=False, strict=False, init_values=False,
                apply_defaults=False, convert=True, validate=False, new=False,
                app_data=None, context=None):
    """
    The import loop is designed to take untrusted data and convert it into the
    native types, as described in ``cls``.  It does this by calling
    ``field_converter`` on every field.

    Errors are aggregated and returned by throwing a ``ModelConversionError``.

    :param cls:
        The class for the model.
    :param instance_or_dict:
        A dict of data to be converted into types according to ``cls``.
    :param field_converter:
        This function is applied to every field found in ``instance_or_dict``.
    :param trusted_data:
        A ``dict``-like structure that may contain already validated data.
    :param partial:
        Allow partial data to validate; useful for PATCH requests.
        Essentially drops the ``required=True`` arguments from field
        definitions. Default: False
    :param strict:
        Complain about unrecognized keys. Default: False
    :param apply_defaults:
        Whether to set fields to their default values when not present in input data.
    :param app_data:
        An arbitrary container for application-specific data that needs to
        be available during the conversion.
    :param context:
        A ``Context`` object that encapsulates configuration options and ``app_data``.
        The context object is created upon the initial invocation of ``import_loop``
        and is then propagated through the entire process.
    """
    if instance_or_dict is None:
        got_data = False
    else:
        got_data = True

    if got_data and not isinstance(instance_or_dict, (cls, dict)):
        raise ConversionError('Model conversion requires a model or dict')

    context = Context._make(context)
    try:
        context.initialized
    except:
        context._setdefaults({
            'initialized': True,
            'field_converter': field_converter,
            'mapping': mapping or {},
            'partial': partial,
            'strict': strict,
            'init_values': init_values,
            'apply_defaults': apply_defaults,
            'convert': convert,
            'validate': validate,
            'new': new,
            'app_data': app_data if app_data is not None else {}
        })

    _model_mapping = context.mapping.get('model_mapping')

    data = dict(trusted_data) if trusted_data else {}
    errors = {}
    # Determine all acceptable field input names
    all_fields = set(cls._fields) ^ set(cls._serializables)
    for field_name, field, in iteritems(cls._fields):
        if field.serialized_name:
            all_fields.add(field.serialized_name)
        if field.deserialize_from:
            all_fields.update(set(listify(field.deserialize_from)))
        if field_name in context.mapping:
            all_fields.update(set(listify(context.mapping[field_name])))

    if got_data and context.strict:
        # Check for rogues if strict is set
        rogue_fields = set(instance_or_dict) - all_fields
        if len(rogue_fields) > 0:
            for field in rogue_fields:
                errors[field] = 'Rogue field'

    for field_name, field in iteritems(cls._fields):

        value = Undefined
        serialized_field_name = field_name

        if got_data:
            trial_keys = listify(field.deserialize_from)
            trial_keys.extend(listify(context.mapping.get(field_name, [])))
            if field.serialized_name:
                serialized_field_name = field.serialized_name
                trial_keys.append(field.serialized_name)
            trial_keys.append(field_name)
            for key in trial_keys:
                if key and key in instance_or_dict:
                    value = instance_or_dict[key]

        if value is Undefined:
            if field_name in data:
                continue
            if context.apply_defaults:
                value = field.default
        if value is Undefined and context.init_values:
            value = None

        if got_data:
            if field.is_compound:
                if _model_mapping:
                    submap = _model_mapping.get(field_name)
                else:
                    submap = {}
                field_context = context._branch(mapping=submap)
            else:
                field_context = context
            try:
                value = context.field_converter(field, value, field_context)
            except (FieldError, CompoundError) as exc:
                errors[serialized_field_name] = exc
                if isinstance(exc, DataError):
                    data[field_name] = exc.partial_data
                continue

        data[field_name] = value

    if errors:
        partial_data = dict(((key, value) for key, value in data.items() if value is not Undefined))
        raise DataError(errors, partial_data)

    return data


def export_loop(cls, instance_or_dict, field_converter=None, role=None, raise_error_on_role=True,
                export_level=None, app_data=None, context=None):
    """
    The export_loop function is intended to be a general loop definition that
    can be used for any form of data shaping, such as application of roles or
    how a field is transformed.

    :param cls:
        The model definition.
    :param instance_or_dict:
        The structure where fields from cls are mapped to values. The only
        expectionation for this structure is that it implements a ``dict``
        interface.
    :param field_converter:
        This function is applied to every field found in ``instance_or_dict``.
    :param role:
        The role used to determine if fields should be left out of the
        transformation.
    :param raise_error_on_role:
        This parameter enforces strict behavior which requires substructures
        to have the same role definition as their parent structures.
    :param app_data:
        An arbitrary container for application-specific data that needs to
        be available during the conversion.
    :param context:
        A ``Context`` object that encapsulates configuration options and ``app_data``.
        The context object is created upon the initial invocation of ``import_loop``
        and is then propagated through the entire process.
    """
    context = Context._make(context)
    try:
        context.initialized
    except:
        context._setdefaults({
            'initialized': True,
            'field_converter': field_converter,
            'role': role,
            'raise_error_on_role': raise_error_on_role,
            'export_level': export_level,
            'app_data': app_data if app_data is not None else {}
        })

    data = {}

    # Translate `role` into `gottago` function
    gottago = wholelist()
    if hasattr(cls, '_options') and context.role in cls._options.roles:
        gottago = cls._options.roles[context.role]
    elif context.role and context.raise_error_on_role:
        error_msg = u'%s Model has no role "%s"'
        raise ValueError(error_msg % (cls.__name__, context.role))
    else:
        gottago = cls._options.roles.get("default", gottago)

    fields_order = (getattr(cls._options, 'fields_order', None)
                    if hasattr(cls, '_options') else None)

    for field_name, field, value in atoms(cls, instance_or_dict):
        serialized_name = field.serialized_name or field_name

        # Skipping this field was requested
        if gottago(field_name, value):
            continue

        _export_level = field.get_export_level(context)

        if _export_level == DROP:
            continue

        elif value not in (None, Undefined):
            value = context.field_converter(field, value, context)

        if value is Undefined:
            if _export_level <= DEFAULT:
                continue
        elif value is None:
            if _export_level <= NOT_NONE:
                continue
        elif field.is_compound and len(value) == 0:
            if _export_level <= NONEMPTY:
                continue

        if value is Undefined:
            value = None

        data[serialized_name] = value

    if fields_order:
        data = sort_dict(data, fields_order)

    return data


def sort_dict(dct, based_on):
    """
    Sorts provided dictionary based on order of keys provided in ``based_on``
    list.

    Order is not guarantied in case if ``dct`` has keys that are not present
    in ``based_on``

    :param dct:
        Dictionary to be sorted.
    :param based_on:
        List of keys in order that resulting dictionary should have.
    :return:
        OrderedDict with keys in the same order as provided ``based_on``.
    """
    return OrderedDict(
        sorted(
            dct.items(),
            key=lambda el: based_on.index(el[0] if el[0] in based_on else -1))
    )


def atoms(cls, instance_or_dict):
    """
    Iterator for the atomic components of a model definition and relevant
    data that creates a 3-tuple of the field's name, its type instance and
    its value.

    :param cls:
        The model definition.
    :param instance_or_dict:
        The structure where fields from cls are mapped to values. The only
        expectation for this structure is that it implements a ``Mapping``
        interface.
    """
    all_fields = itertools.chain(iteritems(cls._fields),
                                 iteritems(cls._serializables))

    return ((field_name, field, instance_or_dict.get(field_name, Undefined))
            for field_name, field in all_fields)



###
# Field filtering
###

class Role(collections.Set):

    """
    A ``Role`` object can be used to filter specific fields against a sequence.

    The ``Role`` is two things: a set of names and a function.  The function
    describes how filter taking a field name as input and then returning either
    ``True`` or ``False``, indicating that field should or should not be
    skipped.

    A ``Role`` can be operated on as a ``Set`` object representing the fields
    is has an opinion on.  When Roles are combined with other roles, the
    filtering behavior of the first role is used.
    """

    def __init__(self, function, fields):
        self.function = function
        self.fields = set(fields)

    def _from_iterable(self, iterable):
        return Role(self.function, iterable)

    def __contains__(self, value):
        return value in self.fields

    def __iter__(self):
        return iter(self.fields)

    def __len__(self):
        return len(self.fields)

    def __eq__(self, other):
        print(dir(self.function))
        return (self.function.__name__ == other.function.__name__ and
                self.fields == other.fields)

    def __str__(self):
        return '%s(%s)' % (self.function.__name__,
                           ', '.join("'%s'" % f for f in self.fields))

    def __repr__(self):
        return '<Role %s>' % str(self)

    # edit role fields
    def __add__(self, other):
        fields = self.fields.union(other)
        return self._from_iterable(fields)

    def __sub__(self, other):
        fields = self.fields.difference(other)
        return self._from_iterable(fields)

    # apply role to field
    def __call__(self, name, value):
        return self.function(name, value, self.fields)

    # static filter functions
    @staticmethod
    def wholelist(name, value, seq):
        """
        Accepts a field name, value, and a field list.  This functions
        implements acceptance of all fields by never requesting a field be
        skipped, thus returns False for all input.

        :param name:
            The field name to inspect.
        :param value:
            The field's value.
        :param seq:
            The list of fields associated with the ``Role``.
        """
        return False

    @staticmethod
    def whitelist(name, value, seq):
        """
        Implements the behavior of a whitelist by requesting a field be skipped
        whenever it's name is not in the list of fields.

        :param name:
            The field name to inspect.
        :param value:
            The field's value.
        :param seq:
            The list of fields associated with the ``Role``.
        """

        if seq is not None and len(seq) > 0:
            return name not in seq
        return True

    @staticmethod
    def blacklist(name, value, seq):
        """
        Implements the behavior of a blacklist by requesting a field be skipped
        whenever it's name is found in the list of fields.

        :param k:
            The field name to inspect.
        :param v:
            The field's value.
        :param seq:
            The list of fields associated with the ``Role``.
        """
        if seq is not None and len(seq) > 0:
            return name in seq
        return False


def wholelist(*field_list):
    """
    Returns a function that evicts nothing. Exists mainly to be an explicit
    allowance of all fields instead of a using an empty blacklist.
    """
    return Role(Role.wholelist, field_list)


def whitelist(*field_list):
    """
    Returns a function that operates as a whitelist for the provided list of
    fields.

    A whitelist is a list of fields explicitly named that are allowed.
    """
    return Role(Role.whitelist, field_list)


def blacklist(*field_list):
    """
    Returns a function that operates as a blacklist for the provided list of
    fields.

    A blacklist is a list of fields explicitly named that are not allowed.
    """
    return Role(Role.blacklist, field_list)



###
# Field converter interface
###

class FieldConverter(object):

    def __call__(self, field, value, context):
        raise NotImplementedError



###
# Standard export converters
###


class ExportConverter(FieldConverter):

    def __init__(self, format, exceptions=None):
        self.primary = format
        self.secondary = not format
        self.exceptions = set(exceptions) if exceptions else None

    def __call__(self, field, value, context):
        format = self.primary
        if self.exceptions:
            if any((issubclass(field.typeclass, cls) for cls in self.exceptions)):
                format = self.secondary
        return field.export(value, format, context)


_to_native_converter = ExportConverter(NATIVE)

_to_dict_converter = ExportConverter(NATIVE, [ModelType])

_to_primitive_converter = ExportConverter(PRIMITIVE)



###
# Standard import converters
###


class ImportConverter(FieldConverter):

    def __init__(self, action):
        self.action = action
        self.method = operator.attrgetter(self.action)

    def __call__(self, field, value, context):
        field.check_required(value, context)
        if value in (None, Undefined):
            return value
        return self.method(field)(value, context)


import_converter = ImportConverter('convert')

validation_converter = ImportConverter('validate')


###
# Context stub factories
###


def get_import_context(**options):
    import_options = {
        'field_converter': import_converter,
        'partial': False,
        'strict': False,
        'convert': True,
        'validate': False
    }
    import_options.update(options)
    return Context(**import_options)



###
# Import and export functions
###


def convert(cls, instance_or_dict, **kwargs):
    return import_loop(cls, instance_or_dict, import_converter, **kwargs)


def to_native(cls, instance_or_dict, **kwargs):
    return export_loop(cls, instance_or_dict, _to_native_converter, **kwargs)


def to_dict(cls, instance_or_dict, **kwargs):
    return export_loop(cls, instance_or_dict, _to_dict_converter, **kwargs)


def to_primitive(cls, instance_or_dict, **kwargs):
    return export_loop(cls, instance_or_dict, _to_primitive_converter, **kwargs)


EMPTY_LIST = "[]"
EMPTY_DICT = "{}"


def expand(data, expanded_data=None):
    """
    Expands a flattened structure into it's corresponding layers.  Essentially,
    it is the counterpart to ``flatten_to_dict``.

    :param data:
        The data to expand.
    :param expanded_data:
        Existing expanded data that this function use for output
    """
    expanded_dict = {}
    context = expanded_data or expanded_dict

    for key, value in iteritems(data):
        try:
            key, remaining = key.split(".", 1)
        except ValueError:
            if value == EMPTY_DICT:
                value = {}
                if key in expanded_dict:
                    continue
            elif value == EMPTY_LIST:
                value = []
                if key in expanded_dict:
                    continue
            expanded_dict[key] = value
        else:
            current_context = context.setdefault(key, {})
            if current_context == []:
                current_context = context[key] = {}
            current_context.update(expand({remaining: value}, current_context))
    return expanded_dict


def flatten_to_dict(instance_or_dict, prefix=None, ignore_none=True):
    """
    Flattens an iterable structure into a single layer dictionary.

    For example:

        {
            's': 'jms was hrrr',
            'l': ['jms was here', 'here', 'and here']
        }

        becomes

        {
            's': 'jms was hrrr',
            u'l.1': 'here',
            u'l.0': 'jms was here',
            u'l.2': 'and here'
        }

    :param instance_or_dict:
        The structure where fields from cls are mapped to values. The only
        expectation for this structure is that it implements a ``Mapping``
        interface.
    :param ignore_none:
        This ignores any ``serialize_when_none`` settings and forces the empty
        fields to be printed as part of the flattening.
        Default: True
    :param prefix:
        This puts a prefix in front of the field names during flattening.
        Default: None
    """
    if isinstance(instance_or_dict, dict):
        iterator = iteritems(instance_or_dict)
    else:
        iterator = enumerate(instance_or_dict)

    flat_dict = {}
    for key, value in iterator:
        if prefix:
            key = ".".join(map(unicode, (prefix, key)))

        if value == []:
            value = EMPTY_LIST
        elif value == {}:
            value = EMPTY_DICT

        if isinstance(value, (dict, list)):
            flat_dict.update(flatten_to_dict(value, prefix=key))
        elif value is not None:
            flat_dict[key] = value
        elif not ignore_none:
            flat_dict[key] = None

    return flat_dict


def flatten(cls, instance_or_dict, role=None, raise_error_on_role=True,
            ignore_none=True, prefix=None, app_data=None, context=None):
    """
    Produces a flat dictionary representation of the model.  Flat, in this
    context, means there is only one level to the dictionary.  Multiple layers
    are represented by the structure of the key.

    Example:

        >>> class Foo(Model):
        ...    s = StringType()
        ...    l = ListType(StringType)

        >>> f = Foo()
        >>> f.s = 'string'
        >>> f.l = ['jms', 'was here', 'and here']

        >>> flatten(Foo, f)
        {'s': 'string', u'l.1': 'jms', u'l.0': 'was here', u'l.2': 'and here'}

    :param cls:
        The model definition.
    :param instance_or_dict:
        The structure where fields from cls are mapped to values. The only
        expectation for this structure is that it implements a ``Mapping``
        interface.
    :param role:
        The role used to determine if fields should be left out of the
        transformation.
    :param raise_error_on_role:
        This parameter enforces strict behavior which requires substructures
        to have the same role definition as their parent structures.
    :param ignore_none:
        This ignores any ``serialize_when_none`` settings and forces the empty
        fields to be printed as part of the flattening.
        Default: True
    :param prefix:
        This puts a prefix in front of the field names during flattening.
        Default: None
    """
    data = to_primitive(cls, instance_or_dict, role=role, raise_error_on_role=raise_error_on_role,
                        export_level=DEFAULT, app_data=app_data, context=context)

    flattened = flatten_to_dict(data, prefix=prefix, ignore_none=ignore_none)

    return flattened

