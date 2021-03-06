# -*- coding: utf-8 -*-

from __future__ import division

from collections import Iterable, Sequence, Mapping
import itertools
import functools

from ..common import *
from ..exceptions import *
from ..models import Model, ModelMeta
from ..undefined import Undefined
from .base import BaseType, get_value_in

from six import iteritems
from six import string_types as basestring
from six import text_type as unicode
from six.moves import xrange


class MultiType(BaseType):

    def __init__(self, **kwargs):
        super(MultiType, self).__init__(**kwargs)
        self.is_compound = True
        if hasattr(self, 'field'):
            self.field.parent_field = self

    def _setup(self, field_name, owner_model):
        # Recursively set up inner fields.
        if hasattr(self, 'field'):
            self.field._setup(None, owner_model)
        super(MultiType, self)._setup(field_name, owner_model)

    def convert(self, value, context):
        raise NotImplementedError

    def export(self, shape_instance, format, context):
        raise NotImplementedError

    def to_native(self, *_, **__):
        raise RuntimeError("This method is no longer implemented by the standard compound types. " \
                           "Please use 'convert()' or 'export()' instead.")

    def to_primitive(self, *_, **__):
        raise RuntimeError("This method is no longer implemented by the standard compound types. " \
                           "Please use 'export()' instead.")

    def init_compound_field(self, field, compound_field, **kwargs):
        """
        Some of non-BaseType fields require a `field` arg.
        To avoid name conflict, provide it as `compound_field`.
        Example:

            comments = ListType(DictType, compound_field=StringType)
        """
        if compound_field:
            field = field(field=compound_field, **kwargs)
        else:
            field = field(**kwargs)
        return field


class ModelType(MultiType):
    """A field that can hold an instance of the specified model."""

    @property
    def fields(self):
        return self.model_class.fields

    def __init__(self, model_spec, **kwargs):

        if isinstance(model_spec, ModelMeta):
            self.model_class = model_spec
            self.model_name = self.model_class.__name__
        elif isinstance(model_spec, basestring):
            self.model_class = None
            self.model_name = model_spec
        else:
            raise TypeError("ModelType: Expected a model, got an argument "
                            "of the type '{}'.".format(model_spec.__class__.__name__))

        super(ModelType, self).__init__(**kwargs)

    def __repr__(self):
        return object.__repr__(self)[:-1] + ' for %s>' % self.model_class

    def _mock(self, context=None):
        return self.model_class.get_mock_object(context)

    def _setup(self, field_name, owner_model):
        # Resolve possible name-based model reference.
        if not self.model_class:
            if self.model_name == owner_model.__name__:
                self.model_class = owner_model
            else:
                raise Exception("ModelType: Unable to resolve model '{}'.".format(self.model_name))
        super(ModelType, self)._setup(field_name, owner_model)

    def pre_setattr(self, value):
        if value is not None \
          and not isinstance(value, Model):
            value = self.model_class(value)
        return value

    def convert(self, value, context):

        if isinstance(value, self.model_class):
            model_class = type(value)
        elif isinstance(value, dict):
            model_class = self.model_class
        else:
            raise ConversionError(
                u'Please use a mapping for this field or {0} instance instead of {1}.'.format(
                    self.model_class.__name__,
                    type(value).__name__))
        return model_class._convert(value, context=context)

    def export(self, model_instance, format, context):
        return model_instance.export(format=format, context=context)


class ListType(MultiType):
    """A field for storing a list of items, all of which must conform to the type
    specified by the ``field`` parameter.
    """

    def __init__(self, field, min_size=None, max_size=None, **kwargs):

        if not isinstance(field, BaseType):
            compound_field = kwargs.pop('compound_field', None)
            field = self.init_compound_field(field, compound_field, **kwargs)

        self.field = field
        self.min_size = min_size
        self.max_size = max_size

        validators = [self.check_length] + kwargs.pop("validators", [])

        super(ListType, self).__init__(validators=validators, **kwargs)

    @property
    def model_class(self):
        return self.field.model_class

    def _mock(self, context=None):
        min_size = self.min_size or 1
        max_size = self.max_size or 1
        if min_size > max_size:
            message = u'Minimum list size is greater than maximum list size.'
            raise MockCreationError(message)
        random_length = get_value_in(min_size, max_size)

        return [self.field._mock(context) for _ in xrange(random_length)]

    def _coerce(self, value):
        if isinstance(value, list):
            return value
        elif isinstance(value, Sequence) and not isinstance(value, basestring):
            return value
        elif isinstance(value, Mapping):
            try:
                value.__reversed__ # indicates an ordered mapping
            except AttributeError:
                return [value[k] for k in sorted(value)]
            else:
                return value.values()
        elif isinstance(value, basestring):
            pass
        elif isinstance(value, Iterable):
            return value
        raise ConversionError('Could not interpret the value as a list')

    def convert(self, value, context):
        value = self._coerce(value)
        data = []
        errors = {}
        for index, item in enumerate(value):
            try:
                data.append(context.field_converter(self.field, item, context))
            except BaseError as exc:
                errors[index] = exc
        if errors:
            raise CompoundError(errors)
        return data

    def check_length(self, value, context):
        list_length = len(value) if value else 0

        if self.min_size is not None and list_length < self.min_size:
            message = ({
                True: u'Please provide at least %d item.',
                False: u'Please provide at least %d items.',
            }[self.min_size == 1]) % self.min_size
            raise ValidationError(message)

        if self.max_size is not None and list_length > self.max_size:
            message = ({
                True: u'Please provide no more than %d item.',
                False: u'Please provide no more than %d items.',
            }[self.max_size == 1]) % self.max_size
            raise ValidationError(message)

    def export(self, list_instance, format, context):
        """Loops over each item in the model and applies either the field
        transform or the multitype transform.  Essentially functions the same
        as `transforms.export_loop`.
        """
        data = []
        _export_level = self.field.get_export_level(context)
        if _export_level == DROP:
            return data
        for value in list_instance:
            shaped = self.field.export(value, format, context)
            if shaped is None:
                if _export_level <= NOT_NONE:
                    continue
            elif self.field.is_compound and len(shaped) == 0:
                if _export_level <= NONEMPTY:
                    continue
            data.append(shaped)
        return data


class DictType(MultiType):
    """A field for storing a mapping of items, the values of which must conform to the type
    specified by the ``field`` parameter.
    """

    def __init__(self, field, coerce_key=None, **kwargs):
        if not isinstance(field, BaseType):
            compound_field = kwargs.pop('compound_field', None)
            field = self.init_compound_field(field, compound_field, **kwargs)

        self.coerce_key = coerce_key or unicode
        self.field = field

        super(DictType, self).__init__(**kwargs)

    @property
    def model_class(self):
        return self.field.model_class

    def convert(self, value, context, safe=False):
        if not isinstance(value, dict):
            raise ConversionError(u'Only dictionaries may be used in a DictType')

        data = {}
        errors = {}
        for k, v in iteritems(value):
            try:
                data[self.coerce_key(k)] = context.field_converter(self.field, v, context)
            except BaseError as exc:
                errors[k] = exc
        if errors:
            raise CompoundError(errors)
        return data

    def export(self, dict_instance, format, context):
        """Loops over each item in the model and applies either the field
        transform or the multitype transform.  Essentially functions the same
        as `transforms.export_loop`.
        """
        data = {}
        _export_level = self.field.get_export_level(context)
        if _export_level == DROP:
            return data
        for key, value in iteritems(dict_instance):
            shaped = self.field.export(value, format, context)
            if shaped is None:
                if _export_level <= NOT_NONE:
                    continue
            elif self.field.is_compound and len(shaped) == 0:
                if _export_level <= NONEMPTY:
                    continue
            data[key] = shaped
        return data


class PolyModelType(MultiType):
    """A field that accepts an instance of any of the specified models."""

    def __init__(self, model_spec, **kwargs):

        if isinstance(model_spec, (ModelMeta, basestring)):
            self.model_classes = (model_spec,)
            allow_subclasses = True
        elif isinstance(model_spec, Iterable):
            self.model_classes = tuple(model_spec)
            allow_subclasses = False
        else:
            raise Exception("The first argument to PolyModelType.__init__() "
                            "must be a model or an iterable.")

        self.claim_function = kwargs.pop("claim_function", None)
        self.allow_subclasses = kwargs.pop("allow_subclasses", allow_subclasses)

        MultiType.__init__(self, **kwargs)

    def __repr__(self):
        return object.__repr__(self)[:-1] + ' for %s>' % str(self.model_classes)

    def _setup(self, field_name, owner_model):
        # Resolve possible name-based model references.
        resolved_classes = []
        for m in self.model_classes:
            if isinstance(m, basestring):
                if m == owner_model.__name__:
                    resolved_classes.append(owner_model)
                else:
                    raise Exception("PolyModelType: Unable to resolve model '{}'.".format(m))
            else:
                resolved_classes.append(m)
        self.model_classes = tuple(resolved_classes)
        super(PolyModelType, self)._setup(field_name, owner_model)

    def is_allowed_model(self, model_instance):
        if self.allow_subclasses:
            if isinstance(model_instance, self.model_classes):
                return True
        else:
            if model_instance.__class__ in self.model_classes:
                return True
        return False

    def convert(self, value, context):

        if value is None:
            return None
        if self.is_allowed_model(value):
            return value
        if not isinstance(value, dict):
            if len(self.model_classes) > 1:
                instanceof_msg = 'one of: {}'.format(', '.join(
                    cls.__name__ for cls in self.model_classes))
            else:
                instanceof_msg = self.model_classes[0].__name__
            raise ConversionError(u'Please use a mapping for this field or '
                                    'an instance of {}'.format(instanceof_msg))

        model_class = self.find_model(value)
        return model_class(value, context=context)

    def find_model(self, data):
        """Finds the intended type by consulting potential classes or `claim_function`."""

        chosen_class = None
        if self.claim_function:
            chosen_class = self.claim_function(self, data)
        else:
            candidates = self.model_classes
            if self.allow_subclasses:
                candidates = itertools.chain.from_iterable(
                                 ([m] + m._subclasses for m in candidates))
            fallback = None
            matching_classes = []
            for cls in candidates:
                match = None
                if '_claim_polymorphic' in cls.__dict__:
                    match = cls._claim_polymorphic(data)
                elif not fallback: # The first model that doesn't define the hook
                    fallback = cls # can be used as a default if there's no match.
                if match:
                    matching_classes.append(cls)
            if not matching_classes and fallback:
                chosen_class = fallback
            elif len(matching_classes) == 1:
                chosen_class = matching_classes[0]
            else:
                raise Exception("Got ambiguous input for polymorphic field")
        if chosen_class:
            return chosen_class
        else:
            raise Exception("Input for polymorphic field did not match any model")

    def export(self, model_instance, format, context):

        model_class = model_instance.__class__
        if not self.is_allowed_model(model_instance):
            raise Exception("Cannot export: {} is not an allowed type".format(model_class))

        return model_instance.export(format=format, context=context)

