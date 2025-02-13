from __future__ import absolute_import, division, print_function

from langkit.diagnostics import check_source_language
from langkit.dsl import (
    ASTNode, AbstractField, AnalysisUnit, AnalysisUnitKind, Annotations, Bool,
    Equation, Field, Int, LexicalEnv, LogicVar, LookupKind as LK,
    NullField, Struct, Symbol, T, UserField, abstract, env_metadata,
    has_abstract_list, synthetic
)
from langkit.envs import (
    EnvSpec, RefKind, add_env, add_to_env, add_to_env_kv, call_env_hook, do,
    handle_children, reference, set_initial_env
)
from langkit.expressions import (
    AbstractKind, AbstractProperty, And, ArrayLiteral as Array, BigIntLiteral,
    Bind, Cond, DynamicVariable, EmptyEnv, Entity, If, Let, Literal, No, Not,
    Or, Property, PropertyError, Self, String, Try, Var, ignore,
    langkit_property, new_env_assoc
)
from langkit.expressions.logic import LogicFalse, LogicTrue, Predicate


env = DynamicVariable('env', LexicalEnv)
origin = DynamicVariable('origin', T.AdaNode)
imprecise_fallback = DynamicVariable('imprecise_fallback', Bool)

UnitSpecification = AnalysisUnitKind.unit_specification
UnitBody = AnalysisUnitKind.unit_body

noprims = {'inherited_primitives': False, 'others': True}


def bind_origin(node, expr):
    """
    Bind the origin iff we're in the definition of an aspect clause where
    sequential lookup needs to be deactivated.
    """
    return origin.bind(If(node.in_aspect, No(T.AdaNode), node), expr)


def default_origin():
    """
    Helper to return an origin dynamic param spec wich defaults to
    No(AdaNode).
    """
    return (origin, No(T.AdaNode))


def default_imprecise_fallback():
    """
    Helper to return an imprecise fallback dynamic param spec which defaults to
    False.
    """
    return (imprecise_fallback, False)


def entity_no_md(type, node, rebindings, from_rebound):
    return Let(lambda n=node: type.entity.new(
        node=n,
        info=If(n.is_null, No(T.entity_info), T.entity_info.new(
            rebindings=rebindings,
            md=No(T.env_md),
            from_rebound=from_rebound
        ))
    ))


def TypeBind(*args, **kwargs):
    check_source_language(
        'eq_prop' not in kwargs.keys(),
        "You cannot pass an eq_prop to TypeBind"
    )
    kwargs['eq_prop'] = BaseTypeDecl.matching_type
    return Bind(*args, **kwargs)


def bool_bind(type_var):
    """
    Decouple the logic of binding to a Boolean type. We use
    matching_formal_prim_type because in name resolution, Ada expects any type
    derived from bool.
    """
    return Bind(type_var, Self.bool_type,
                eq_prop=BaseTypeDecl.matching_formal_prim_type)


def universal_int_bind(type_var):
    """
    Return an equation that will bind type_var to any integer value,
    corresponding to the notion of universal_integer in the Ada RM.
    """
    return TypeBind(type_var, Self.universal_int_type)


def universal_real_bind(type_var):
    return TypeBind(type_var, Self.universal_real_type)


def ref_used_packages():
    """
    If Self is a library item or a subunit, reference the environments for
    packages that are used at the top-level here. See
    UsePackageClause's ref_env_nodes for the rationale.
    """
    return reference(
        Self.top_level_use_package_clauses,
        through=T.Name.use_package_name_designated_env,
        cond=Self.parent.is_a(T.LibraryItem, T.Subunit)
    )


def populate_dependent_units():
    return do(If(
        Self.is_unit_root,
        Self.top_level_with_package_clauses.map(
            lambda package_name:
            # First fetch the spec
            package_name.referenced_unit(UnitSpecification)
            # If no spec exists, maybe it is a library level subprogram with
            # just a body, so fetch the body.
            .root._or(package_name.referenced_unit(UnitBody).root)
        ),
        No(AdaNode.array)
    ))


def ref_generic_formals():
    """
    If Self is a generic package/subprogram and not a library item,
    then the generic formals are not available in parent
    environments. Make them available with ref_envs.
    """
    return reference(
        Self.cast(T.AdaNode)._.singleton,
        through=T.AdaNode.nested_generic_formal_part,
        cond=Not(Self.is_unit_root),
        kind=RefKind.prioritary,
        shed_corresponding_rebindings=True,
    )


def env_mappings(defining_names, entity, dest_env=None):
    """
    Creates an env mapping array from a list of BaseId to be used as keys, and
    an entity to be used as value in the mappings.
    """
    return defining_names.map(
        lambda n: new_env_assoc(
            key=n.name_symbol, val=entity, dest_env=dest_env
        )
    )


def env_get(env, symbol, lookup=None, from_node=No(T.AdaNode),
            categories=None):
    """
    Wrapper for env.get. Refines from_node so that it starts from the closest
    BasicSubpDecl / GenericInstantiation.
    (see AdaNode.env_get_real_from_node).
    """
    return env.get(
        symbol, lookup, Self.env_get_real_from_node(from_node), categories
    )


def env_get_first(env, symbol, lookup=None, from_node=No(T.AdaNode),
                  categories=None):
    """
    Wrapper for env.get_first. Refines from_node so that it starts from the
    closest BasicSubpDecl / GenericInstantiation.
    (see AdaNode.env_get_real_from_node).
    """
    return env.get_first(
        symbol, lookup, Self.env_get_real_from_node(from_node), categories
    )


def new_metadata(**kwargs):
    """
    Constructor for Metadata. Waiting on default values for structs.
    """
    source = None
    if "source" in kwargs:
        source = kwargs["source"]
        del kwargs["source"]

    vals = [
        ("dottable_subp", False),
        ("primitive", No(T.AdaNode)),
        ("primitive_real_type", No(T.AdaNode)),
        ("access_entity", False),
    ]

    for k, v in vals:
        if k not in kwargs:
            kwargs[k] = v if not source else getattr(source, k)

    return T.Metadata.new(**kwargs)


@env_metadata
class Metadata(Struct):
    dottable_subp = UserField(
        Bool, doc="Whether the stored element is a subprogram accessed through"
                  " the dot notation"
    )
    access_entity = UserField(
        Bool,
        doc="Whether the accessed entity is an anonymous access to it or not."
    )
    primitive = UserField(
        T.AdaNode,
        doc="The type for which this subprogram is a primitive, if any"
    )
    primitive_real_type = UserField(
        T.AdaNode,
        doc="The type for which this subprogram is a primitive, if any"
    )


@abstract
class AdaNode(ASTNode):
    """
    Root node class for the Ada syntax tree.
    """

    annotations = Annotations(
        generic_list_type='AdaList',
        warn_on_node=True
    )

    declarative_scope = Property(
        Self.parents.find(
            lambda p: p.is_a(T.DeclarativePart)
        ).cast(T.DeclarativePart),
        doc="Return the scope of definition of this basic declaration.",
        ignore_warn_on_node=True,
        public=True
    )

    @langkit_property(return_type=T.String)
    def custom_id_text():
        """
        Custom Unique identifying text used to recognize this node. Not
        applicable to all nodes, but on AdaNode because it spans more than one
        hierarchy of node types.
        """
        return String("")

    in_aspect = Property(Not(Self.parents.find(
        lambda p: p.cast(T.AspectAssoc).then(
            lambda a: a.id.as_bare_entity.name_symbol.any_of('Pre', 'Post')
        )
    ).is_null))

    empty_env = Property(
        Self.parents.find(lambda p: p.is_a(T.CompilationUnit))
        .cast(T.CompilationUnit).get_empty_env,
    )

    @langkit_property(return_type=T.String)
    def string_join(strns=T.String.array, sep=T.String):
        """
        Static method. Return the array of strings joined by separator ``sep``.
        """
        arr_len = Var(strns.length)

        return strns.mapcat(lambda i, n: (
            If(i == arr_len - 1, n, n.concat(sep))
        ))

    @langkit_property(return_type=T.String)
    def sym_join(syms=Symbol.array, sep=T.String):
        """
        Static method. Return the array of symbols joined by separator ``sep``.
        """
        return Entity.string_join(syms.map(lambda s: s.image), sep)

    @langkit_property(return_type=T.BasicDecl,
                      ignore_warn_on_node=True, uses_entity_info=False)
    def get_root_decl():
        """
        Unit method. Return the root decl for this node's unit.
        """
        return Self.unit.root._.match(
            lambda cu=T.CompilationUnit: cu.body.match(
                lambda su=T.Subunit: su.body,
                lambda li=T.LibraryItem: li.item,
                lambda _: No(T.BasicDecl)
            ),
            lambda _: No(T.BasicDecl),
        )

    @langkit_property(return_type=Bool)
    def is_children_env(parent=LexicalEnv, current_env=LexicalEnv):
        """
        Static property. Will return True if current_env is a children of
        parent.
        """
        return Cond(
            parent == EmptyEnv, False,
            current_env == parent, True,
            current_env.is_null, False,
            Self.is_children_env(parent, current_env.env_parent)
        )

    @langkit_property(return_type=T.AdaNode.entity)
    def trigger_access_entity(val=T.Bool):
        """
        Return Self as an entity, but with the ``access_entity`` field set to
        val. Helper for the 'Unrestricted_Access machinery.
        """
        new_md = Var(new_metadata(source=Entity.info.md, access_entity=val))

        return AdaNode.entity.new(
            node=Entity.node, info=T.entity_info.new(
                rebindings=Entity.info.rebindings,
                md=new_md,
                from_rebound=Entity.info.from_rebound
            )
        )

    @langkit_property(public=True, return_type=T.DefiningName.entity,
                      dynamic_vars=[default_imprecise_fallback()])
    def xref():
        """
        Return a cross reference from this node to a defining identifier.
        """
        return No(T.DefiningName.entity)

    @langkit_property(public=True, return_type=T.BasicDecl.entity.array)
    def complete():
        """
        Return possible completions at this point in the file.
        """
        return Self.children_env.get(No(Symbol)).map(
            lambda n: n.cast(T.BasicDecl)
        )

    @langkit_property(public=False,
                      dynamic_vars=[default_imprecise_fallback()])
    def referenced_decl_internal_helper(ref_var=T.LogicVar,
                                        try_immediate=Bool):
        """
        Helper to generate the piece of logic that is common to all
        ``referenced_decl_internal`` implementations. ``ref_var`` is the logic
        variable that contains the reference to return.
        """
        return If(
            imprecise_fallback,
            Let(lambda v=Try(
                Self.logic_val(Entity, ref_var, try_immediate),
                LogicValResult.new(success=False, value=No(AdaNode.entity))
            ): Let(
                lambda decl=v.value.cast(T.BasicDecl.entity): If(
                    v.success & (decl == v.value),
                    decl,
                    Entity.cast(T.Expr)._.first_corresponding_decl
                )
            )),
            Self.logic_val(Entity, ref_var, try_immediate)
                .value.cast_or_raise(T.BasicDecl.entity)
        ).then(lambda x: x.match(
            # If the logic variable is bound to a GenericSubpInternal, retrieve
            # the instantiation leading to it instead.
            lambda g=T.GenericSubpInternal: T.BasicDecl.entity.new(
                node=g.info.rebindings.new_env.env_node
                .cast_or_raise(T.GenericInstantiation),
                info=T.entity_info.new(
                    # Since we return the instantiation itself, remove it from
                    # its rebindings.
                    rebindings=x.info.rebindings.get_parent,
                    from_rebound=x.info.from_rebound,
                    md=new_metadata()
                )
            ),
            lambda _: x
        ))

    @langkit_property(public=True)
    def generic_instantiations():
        """
        Return the potentially empty list of generic package/subprogram
        instantiations that led to the creation of this entity. Outer-most
        instantiations appear last.
        """
        return Self.generic_instantiations_internal(Entity.info.rebindings)

    @langkit_property(return_type=T.GenericInstantiation.entity.array)
    def generic_instantiations_internal(r=T.EnvRebindings):
        return If(
            r == No(T.EnvRebindings),
            No(T.GenericInstantiation.entity.array),

            Let(lambda
                head=(r.new_env.env_node
                      .cast_or_raise(T.GenericInstantiation).as_bare_entity),
                tail=Self.generic_instantiations_internal(r.get_parent):
                head.singleton.concat(tail))
        )

    # We mark this property as memoizable because for the moment, we only ever
    # get the first result of logic resolution, so we only ever want the result
    # of the first evaluation of this property. When we change that, we'll
    # probably change the solving API anyway.
    @langkit_property(call_memoizable=True, return_type=T.LogicValResult)
    def logic_val(from_node=T.AdaNode.entity, lvar=LogicVar,
                  try_immediate=(Bool, False)):
        success = Var(If(
            try_immediate & Not(lvar.get_value.is_null),
            True,
            from_node.resolve_names_from_closest_entry_point
        ))

        return LogicValResult.new(success=success, value=If(
            success, lvar.get_value, No(T.AdaNode.entity)
        ))

    @langkit_property(return_type=T.AdaNode.entity)
    def semantic_parent_helper(env=LexicalEnv):
        return env.then(lambda env: env.env_node.as_entity._or(
            Entity.semantic_parent_helper(env.env_parent)
        ))

    @langkit_property(public=True)
    def semantic_parent():
        """
        Return the semantic parent for this node, if applicable, null
        otherwise.
        """
        return Entity.semantic_parent_helper(Entity.node_env)

    @langkit_property(
        return_type=AnalysisUnit, external=True, uses_entity_info=False,
        uses_envs=False,
        call_non_memoizable_because='Getting an analysis unit cannot appear'
                                    ' in a memoized context'
    )
    def get_unit(name=Symbol.array, kind=AnalysisUnitKind,
                 load_if_needed=Bool):
        """
        Return the analysis unit for the given ``kind`` corresponding to this
        Name. Return null if this is an illegal unit name, or if
        ``load_if_needed`` is false and the unit is not loaded yet.
        """
        pass

    @langkit_property(return_type=T.AdaNode, uses_entity_info=False,
                      ignore_warn_on_node=True, call_memoizable=True)
    def get_unit_root_decl(name=Symbol.array, kind=AnalysisUnitKind):
        """
        If the corresponding analysis unit is loaded, return the root decl
        node for the given analysis unit ``kind`` and correpsonding to the
        name ``name``. If it's not loaded, return none.
        """
        # Because we don't load the unit when it's not already there, it is
        # safe to use this property in a memoized context.
        u = Var(Self.get_unit(name, kind, False))

        return u._.root._.get_root_decl

    @langkit_property(public=True, return_type=AnalysisUnit.array,
                      external=True, uses_entity_info=False, uses_envs=False)
    def filter_is_imported_by(units=AnalysisUnit.array, transitive=Bool):
        """
        Filters out among the list of given units those that cannot refer to
        the unit in which this node lies. If transitive is True, the whole
        transitive closure of imports will be used to find a reference to the
        unit of this node.
        """
        pass

    @langkit_property(return_type=AnalysisUnit.array)
    def unique_units(list_of_units=AnalysisUnit.array):
        """
        Remove the duplicate units from the given list of analysis units.
        """
        return Self.unique_units_impl(list_of_units, 0, No(AnalysisUnit.array))

    @langkit_property(return_type=AnalysisUnit.array)
    def unique_units_impl(list_of_units=AnalysisUnit.array, i=Int,
                          result=AnalysisUnit.array):
        return If(
            i < list_of_units.length,
            Self.unique_units_impl(
                list_of_units,
                i + 1,
                If(result.contains(list_of_units.at(i)),
                   result,
                   result.concat(list_of_units.at(i).singleton))
            ),
            result
        )

    @langkit_property(kind=AbstractKind.abstract_runtime_check,
                      return_type=Equation, dynamic_vars=[env, origin])
    def xref_equation():
        """
        This is the base property for constructing equations that, when solved,
        will resolve names and types for every sub expression of the expression
        you call it on. Note that if you call that on any expression, in some
        context it might lack full information and return multiple solutions.
        If you want completely precise resolution, you must call that on the
        outermost node that supports xref_equation.
        """
        pass

    xref_stop_resolution = Property(False)
    stop_resolution_equation = Property(LogicTrue())

    @langkit_property(return_type=Equation, dynamic_vars=[env, origin])
    def sub_equation():
        """
        Wrapper for xref_equation, meant to be used inside of xref_equation
        when you want to get the sub equation of a sub expression. It is
        used to change the behavior when xref_equation is called from
        another xref_equation call, or from the top level, so that we can do
        resolution in several steps.
        """
        return If(Entity.xref_stop_resolution,
                  Entity.stop_resolution_equation,
                  Entity.xref_equation)

    @langkit_property(return_type=Bool, dynamic_vars=[env, origin])
    def resolve_names_internal(initial=Bool, additional_equation=Equation):
        """
        Internal helper for resolve_names, implementing the recursive logic.
        """

        solve_xref = Var(initial | Self.xref_stop_resolution)

        i = Var(If(
            solve_xref,
            (Entity.xref_equation & additional_equation).solve,
            True)
        )

        j = Var(Self.children.all(lambda c: c.then(
            # Do not explore nodes that are xref entry points, and are not the
            # initial node.
            lambda c: If(
                c.xref_entry_point,
                True,
                c.as_entity.resolve_names_internal(False, LogicTrue()),
            ),
            default_val=True
        )))

        return i & j

    xref_entry_point = Property(
        False,
        public=True,
        doc="""
        Designates entities that are entry point for the xref solving
        infrastructure. If this returns true, then resolve_names can be called
        on it.
        """
    )

    @langkit_property(return_type=Bool, public=True,
                      memoized=True, call_memoizable=True)
    def resolve_names():
        """
        This will resolve names for this node. If the operation is successful,
        then type_var and ref_var will be bound on appropriate subnodes of the
        statement.
        """
        return env.bind(
            Entity.children_env,
            bind_origin(Self, Entity.resolve_names_internal(True, LogicTrue()))
        )

    resolve_names_from_closest_entry_point = Property(
        Entity.parents.find(lambda p: p.xref_entry_point).resolve_names
    )

    # TODO: Navigation properties are not ready to deal with units containing
    # multiple packages.

    body_unit = Property(
        Self.top_level_decl(Self.unit)._.match(
            lambda body=T.Body: body.unit,
            lambda decl=T.BasicDecl:
                decl.as_bare_entity.defining_name.referenced_unit(UnitBody),
        ),

        public=True, doc="""
        If this unit has a body, fetch and return it.
        """
    )

    spec_unit = Property(
        Self.top_level_decl(Self.unit)
        .cast(T.Body)._.as_bare_entity.defining_name
        .referenced_unit_or_null(UnitSpecification),

        public=True, doc="""
        If this unit has a spec, fetch and return it. Return the null analysis
        unit otherwise. Note that this returns null for specs, as they don't
        have another spec themselves.
        """
    )

    @langkit_property(return_type=LexicalEnv)
    def parent_unit_env_helper(unit=AnalysisUnit, env=LexicalEnv):
        return env.env_parent.then(lambda parent_env: parent_env.env_node.then(
            lambda parent_node: If(
                parent_node.unit == unit,
                Self.parent_unit_env_helper(unit, parent_env),
                parent_env
            )
        ))

    @langkit_property()
    def parent_unit_env(env=LexicalEnv):
        """
        Given env's AnalysisUnit, return the first env that has a different
        analysis unit in the env parent chain.
        """
        return env.then(
            lambda env: Self.parent_unit_env_helper(env.env_node.unit, env)
        )

    @langkit_property(return_type=T.AnalysisUnit, public=True,
                      external=True, uses_entity_info=False, uses_envs=False)
    def standard_unit():
        """
        Static method. Return the analysis unit corresponding to the Standard
        package.
        """
        pass

    std = Property(
        Self.standard_unit.root.cast(T.CompilationUnit)
        .body.cast(T.LibraryItem).item.as_bare_entity,
        doc="""
        Retrieves the package corresponding to the Standard unit. Used to
        access standard types.
        """
    )

    std_env = Property(
        Self.std.children_env,
        doc="Get the children env of the Standard package."
    )

    std_entity = Property(
        lambda sym=Symbol: Self.unit.root.std_entity_implem(sym),
        public=True,
        doc="Static property. Return an entity from the standard package"
            " with name `sym`."
    )

    std_entity_implem = Property(
        lambda sym=Symbol: Self.std_env.get_first(sym, categories=noprims),
        memoized=True
    )

    bool_type = Property(
        Self.std_entity('Boolean'), public=True, doc="""
        Static method. Return the standard Boolean type.
        """
    )
    int_type = Property(
        Self.std_entity('Integer'), public=True, doc="""
        Static method. Return the standard Integer type.
        """
    )
    universal_int_type = Property(
        Self.std_entity('Universal_Int_Type_'), public=True, doc="""
        Static method. Return the standard Universal Integer type.
        """
    )
    universal_real_type = Property(
        Self.std_entity('Universal_Real_Type_'), public=True, doc="""
        Static method. Return the standard Universal Real type.
        """
    )

    exc_id_type = Property(
        Self
        .get_unit_root_decl(['Ada', 'Exceptions'], UnitSpecification)
        ._.children_env.get_first('Exception_Id', lookup=LK.flat)
        .cast(T.BaseTypeDecl), doc="""
        Return the type Ada.Exceptions.Exception_Id.
        """

    )

    task_id_type = Property(
        Self.get_unit_root_decl(['Ada', 'Task_Identification'],
                                UnitSpecification)
        ._.children_env.get_first('Task_Id', lookup=LK.flat)
        .cast(T.BaseTypeDecl), doc="""
        Return the type Ada.Task_Identification.Task_Id.
        """
    )

    @langkit_property(return_type=Bool)
    def has_with_visibility(refd_unit=AnalysisUnit):
        """
        Return whether Self's unit has ``with visibility`` on ``refd_unit``.

        In other words, whether Self's unit has a WITH clause on ``refd_unit``,
        or if its spec, or one of its parent specs has one.
        """
        return Or(
            refd_unit.is_referenced_from(Self.unit),
            Self.parent_unit_env(
                # Here we go and explicitly grab the top level item, rather
                # than use Self's children env, because of use clauses, that
                # can be at the top level but semantically belong to the env of
                # the top level item.
                Self.top_level_decl(Self.unit).children_env
            )
            .env_node._.has_with_visibility(refd_unit)
        )

    @langkit_property(return_type=Bool)
    def has_visibility(other_entity=T.AdaNode.entity):
        return Or(
            # The node is a generic package instantiation coming from a formal
            # package.
            other_entity.cast(GenericPackageInstantiation)._.info.from_rebound,

            # The node is not an unit root
            Not(other_entity.cast(T.BasicDecl).is_unit_root),

            # Else, check with visibility
            Self.has_with_visibility(other_entity.node.unit)
        )

    @langkit_property()
    def resolve_generic_actual():
        """
        Helper property to resolve the actuals of generic instantiations.
        """
        return Entity.match(
            lambda te=T.TypeExpr.entity: bind_origin(Self, te.designated_type),

            # TODO: depending on the formal that matches this actual, this name
            # can be both an object or a type. For now, we assume it's a type
            # but we should handle objects too.
            lambda n=T.Name.entity: n.name_designated_type.cast(T.entity)._or(
                # If we don't find a type, find something else
                env.bind(n.children_env, n.env_elements.at(0))
            ),

            lambda _: No(T.entity),
        )

    @langkit_property()
    def top_level_use_package_clauses():
        """
        If Self is a library item or a subunit, return a flat list of all names
        for top-level UsePackageClause nodes. See
        UsePackageClause.env_spec.ref_envs for more details.
        """
        return (
            Self.parent.parent.cast_or_raise(T.CompilationUnit)
            .prelude
            .filter(lambda p: p.is_a(UsePackageClause))
            .mapcat(
                lambda p: p.cast_or_raise(UsePackageClause).packages.map(
                    lambda n: n.cast(AdaNode)
                )
            )
        )

    @langkit_property(return_type=T.Name.array)
    def top_level_with_package_clauses():
        """
        If Self is a library item or a subunit, return a flat list of all
        package names that are with'ed by top-level WithClause nodes.
        """
        return (
            Self.parent.parent.cast_or_raise(T.CompilationUnit)
            .prelude
            .filter(lambda p: p.is_a(WithClause))
            .mapcat(
                lambda p: p.cast_or_raise(WithClause).packages.map(lambda n: n)
            )
        )

    @langkit_property()
    def use_packages_in_spec_of_subp_body():
        """
        If Self is a library-level SubpBody, fetch the environments USE'd in
        its declaration.
        """
        return Let(lambda subpb=Self.cast(T.SubpBody): If(
            subpb.parent.is_a(T.LibraryItem),

            subpb.as_bare_entity.decl_part.then(
                lambda subp_decl: subp_decl.top_level_use_package_clauses.map(
                    lambda use_name:
                    bind_origin(use_name, env.bind(
                        use_name.node_env,
                        use_name.cast_or_raise(T.Name)
                        .as_bare_entity.designated_env
                    ))
                ).env_group(),
                default_val=EmptyEnv
            ),

            EmptyEnv
        ))

    @langkit_property()
    def nested_generic_formal_part():
        """
        Assuming Self is a generic entity's body that is nested (not a library
        item), return the lexical environment for the corresponding
        GenericPackageDecl (or GenericSubpDecl) node. Return an empty
        environment in all other cases.

        This is a helper for generic formals visibility in generic bodies. See
        the use in the child_unit macro.

        The following property is evaluated each time we make a recursive
        lexical environment lookup on a child unit. As it does itself a lot of
        lookups, memoizing it is very important.
        """
        gen_decl = Var(Self.as_bare_entity.match(
            lambda pkg_body=T.PackageBody:
                pkg_body.decl_part.then(
                    lambda d: d.node.parent.cast(T.GenericPackageDecl)
                ),
            lambda bod=T.BaseSubpBody:
                # We're only searching for generics. We look at index 1 and
                # 2, because if self is a subunit, the first entity we find
                # will be the separate declaration. NOTE: We don't use
                # decl_part/previous_part on purpose: They can cause env
                # lookups, hence doing an infinite recursion.
                bod.children_env.env_parent.get(
                    bod.name_symbol, categories=noprims
                ).then(
                    lambda results:
                    results.at(1).node.cast(T.GenericSubpDecl)._or(
                        results.at(2).node.cast(T.GenericSubpDecl)
                    )
                ).cast(T.AdaNode),
            lambda _: No(T.AdaNode)
        ))

        return gen_decl.then(
            lambda gd: gd.children_env, default_val=Self.empty_env
        )

    @langkit_property()
    def is_package():
        """
        Property helper to determine if an entity is a package or not.
        """
        return Self.is_a(PackageDecl, PackageBody, GenericPackageInstantiation,
                         PackageRenamingDecl, GenericPackageDecl)

    @langkit_property()
    def initial_env():
        """
        Provide a lexical environment to use in EnvSpec's initial_env.
        """
        return Self.parent.then(lambda p: p.children_env,
                                default_val=Self.children_env)

    @langkit_property(ignore_warn_on_node=True, public=True)
    def top_level_decl(unit=AnalysisUnit):
        """
        Static method. Get the top-level decl in ``unit``.  This is the body of
        a Subunit, or the item of a ``LibraryItem``.
        """
        return unit._.root.then(
            lambda root:
                root.cast_or_raise(T.CompilationUnit).body.match(
                    lambda li=T.LibraryItem: li.item,
                    lambda su=T.Subunit: su.body,
                    lambda _: No(T.BasicDecl),
                )
        )

    @langkit_property()
    def unpack_formals(formal_params=T.BaseFormalParamDecl.entity.array):
        """
        Static method. Couples (identifier, param spec) for all parameters.
        """
        return Self.unit.root.unpack_formals_impl(formal_params)

    @langkit_property()
    def unpack_formals_impl(formal_params=T.BaseFormalParamDecl.entity.array):
        return formal_params.mapcat(
            lambda spec: spec.defining_names.map(lambda id: SingleFormal.new(
                name=id, spec=spec
            ))
        )

    @langkit_property(return_type=T.ParamMatch.array)
    def match_formals(formal_params=T.BaseFormalParamDecl.entity.array,
                      params=T.AssocList.entity,
                      is_dottable_subp=Bool):
        """
        Static method. For each ParamAssoc in a AssocList, return whether we
        could find a matching formal in Self, and whether this formal is
        optional (i.e. has a default value).
        """
        def matches(formal, actual):
            return ParamMatch.new(has_matched=True,
                                  formal=formal, actual=actual)

        unpacked_formals = Var(Self.unpack_formals(formal_params))

        return params.then(lambda p: p.unpacked_params.map(lambda i, a: If(
            a.name.is_null,

            Let(lambda idx=If(is_dottable_subp, i + 1, i):
                # Positional parameter case: if this parameter has no
                # name association, make sure we have enough formals.
                unpacked_formals.at(idx).then(lambda sp: matches(sp, a))),

            # Named parameter case: make sure the designator is
            # actually a name and that there is a corresponding
            # formal.
            a.name.then(lambda id: (
                unpacked_formals.find(lambda p: p.name.matches(id)).then(
                    lambda sp: matches(sp, a)
                )
            ))
        )))

    @langkit_property(public=True, dynamic_vars=[default_imprecise_fallback()])
    def gnat_xref():
        """
        Return a cross reference from this name to a defining identifier,
        trying to mimic GNAT's xrefs as much as possible.
        """

        bd = Var(Entity.cast(T.Name).enclosing_defining_name
                 .then(lambda dn: dn.basic_decl))

        return Cond(
            bd.then(lambda bd: bd.is_a(T.ParamSpec))
            & bd.semantic_parent.is_a(T.SubpDecl, T.ExprFunction,
                                      T.GenericSubpInternal,
                                      T.BaseTypeDecl),
            bd.semantic_parent.cast(T.BasicDecl).defining_name,

            bd.then(lambda bd: bd.is_a(T.DiscriminantSpec)),
            bd.semantic_parent.cast(T.BasicDecl).defining_name,

            bd.then(lambda bd: bd.is_a(T.ParamSpec))
            & bd.semantic_parent.is_a(T.AbstractSubpDecl, T.FormalSubpDecl,
                                      T.NullSubpDecl),
            bd.semantic_parent.cast(T.BasicDecl).defining_name,

            bd.then(lambda bd: bd.is_a(T.AbstractSubpDecl)),
            bd.cast(T.AbstractSubpDecl).subp_decl_spec
            .first_primitive_subp_of.defining_name,

            bd.then(lambda bd: bd.is_a(T.BasicSubpDecl)),
            bd.cast(T.BasicSubpDecl).subp_decl_spec.first_primitive_subp_of
            .then(
                lambda prim_typ:
                prim_typ.is_tagged_type.then(
                    lambda _: prim_typ.private_completion.then(
                        lambda pc: pc.defining_name
                    )._or(prim_typ.defining_name)
                )
            ),

            Let(lambda ret=Entity.xref: ret.then(
                lambda _:
                Let(lambda dbd=ret.basic_decl: Cond(
                    dbd.is_a(T.ParamSpec),
                    dbd.cast(T.ParamSpec).decl_param(ret),

                    dbd.is_a(T.GenericSubpInternal, T.GenericPackageInternal),
                    dbd.generic_instantiations.at(0).then(
                        lambda gi: gi.cast_or_raise(T.BasicDecl).defining_name,
                        default_val=ret
                    ),

                    dbd.is_a(T.ObjectDecl),
                    dbd.cast(T.ObjectDecl).public_part_decl.then(
                        lambda ppd: ppd.defining_name
                    )._or(ret),


                    dbd.is_a(T.BaseSubpBody),
                    dbd.cast(T.BaseSubpBody)
                    .decl_part._or(dbd).defining_name,

                    ret
                ))
            ))
        )

    @langkit_property(return_type=T.AdaNode, ignore_warn_on_node=True)
    def env_get_real_from_node(from_node=T.AdaNode):
        """
        Static property. Finds the closest BasicSubpDecl /
        GenericInstantiation. Is used by env_get and env_get_first wrappers to
        refine from_node. The reason is that inside a declaration named D,
        one can refer to previous declarations named D. But an env lookup
        from a node inside D would return that D itself, not previous
        declarations.
        """
        return If(from_node.is_null, from_node, Let(
            lambda c=from_node.parents.find(
                lambda n: n.is_a(T.BasicSubpDecl, T.GenericInstantiation)
            ): If(c.is_null, from_node, c)
        ))


def child_unit(name_expr, scope_expr, dest_env=None,
               transitive_parent=False, more_rules=[]):
    """
    This macro will add the properties and the env specification necessary
    to make a node implement the specification of a library child unit in
    Ada, so that you can declare new childs to an unit outside of its own
    scope.

    :param AbstractExpression name_expr: The expression that will retrieve
        the name symbol for the decorated node.

    :param AbstractExpression scope_expr: The expression that will retrieve the
        scope node for the decorated node. If the scope node is not found, it
        should return EmptyEnv: in this case, the actual scope will become the
        root environment.

    :rtype: EnvSpec
    """
    more_rules = list(more_rules)

    return EnvSpec(
        call_env_hook(Self),
        set_initial_env(
            env.bind(Self.initial_env, Let(
                lambda scope=scope_expr: If(scope == EmptyEnv, env, scope)
            ))
        ),
        add_to_env(
            dest_env.then(lambda env:
                          new_env_assoc(key=name_expr, val=Self, dest_env=env))
            if dest_env is not None
            else new_env_assoc(key=name_expr, val=Self)
        ),
        add_env(transitive_parent=transitive_parent),
        populate_dependent_units(),
        ref_used_packages(),
        ref_generic_formals(),
        *more_rules
    )


class DocAnnotation(Struct):
    """
    Documentation annotation.
    """
    key = UserField(T.String, doc="Annotation key")
    value = UserField(T.String, doc="Annotation value")


@abstract
class BasicDecl(AdaNode):
    """
    Root class for an Ada declaration (RM 3.1). A declaration associates a name
    with a language entity, for example a type or a variable.
    """

    @langkit_property(public=True, external=True,
                      return_type=DocAnnotation.array,
                      uses_entity_info=False, uses_envs=False)
    def doc_annotations():
        """
        Return the documentation annotations associated with this decl.
        Annotations are any comment line of the form::

            --% [annotation_name]: [annotation]

        Raises a property error if the doc is incorrectly formatted.

        .. ATTENTION:: This is an experimental feature, so even if it is
           exposed to allow experiments, it is totally unsupported and the API
           and behavior are very likely to change in the future.
        """
        pass

    @langkit_property(public=True, external=True, return_type=T.String,
                      uses_entity_info=False, uses_envs=False)
    def doc():
        """
        Return the documentation associated with this decl. Raises a property
        error if the doc is incorrectly formatted.

        .. ATTENTION:: This is an experimental feature, so even if it is
           exposed to allow experiments, it is totally unsupported and the API
           and behavior are very likely to change in the future.
        """
        pass

    @langkit_property(public=True)
    def previous_part_for_decl():
        """
        Return the previous part for this decl, if applicable.

        .. note:: It is not named previous_part, because BaseTypeDecl has a
            more precise version of previous_part that returns a BaseTypeDecl.
            Probably, we want to rename the specific versions, and have the
            root property be named previous_part. (TODO R925-008)
        """
        return Entity.match(
            lambda btd=T.BaseTypeDecl:
            btd.previous_part(True).cast(T.BasicDecl),
            lambda bd=T.Body: bd.previous_part,
            lambda _: No(T.BasicDecl.entity)
        )

    @langkit_property(public=True, return_type=T.BasicDecl.entity)
    def canonical_part():
        """
        Return the canonical part for this decl. In the case of decls composed
        of several parts, the canonical part will be the first part.
        """
        return Entity.previous_part_for_decl.then(
            lambda pp: pp.canonical_part, default_val=Entity
        )

    @langkit_property(public=True, dynamic_vars=[default_imprecise_fallback()])
    def is_static_decl():
        """
        Return whether this declaration is static.
        """
        return False

    @langkit_property(return_type=T.BasicDecl.entity)
    def unshed_rebindings(rebindings=T.EnvRebindings):
        """
        Put ``rebindings`` back on ``Entity`` if ``Entity`` is rebound
        somewhere in the chain of rebindings. Ensure coherency, eg. that if
        Entity already has some rebindings, the one that we add are a superset
        of the one it already has.
        """
        return Cond(
            rebindings == No(T.EnvRebindings),
            Entity,

            rebindings.old_env.env_node == Self,

            If(
                Or(
                    Entity.info.rebindings == No(T.EnvRebindings),
                    rebindings.get_parent == Entity.info.rebindings,
                ),

                BasicDecl.entity.new(
                    node=Self,
                    info=T.entity_info.new(
                        rebindings=rebindings,
                        md=Entity.info.md,
                        from_rebound=Entity.info.from_rebound
                    )
                ),
                PropertyError(BasicDecl.entity, "Incorrect rebindings")
            ),

            Entity.unshed_rebindings(rebindings.get_parent)
        )

    @langkit_property(public=True)
    def is_imported():
        """
        Whether this declaration is imported from another language.
        """
        return Or(
            Not(Entity.get_aspect('Import').is_null),
            Not(Entity.get_pragma('Import').is_null),
            Not(Entity.get_pragma('Interface').is_null),
        )

    decl_private_part = Property(Entity.match(
        lambda bpd=T.BasePackageDecl: bpd.private_part,
        lambda ttd=T.TaskTypeDecl: ttd.definition.private_part,
        lambda td=T.SingleTaskDecl:     td.task_type.definition.private_part,
        lambda ptd=T.ProtectedTypeDecl: ptd.definition.private_part,
        lambda spd=T.SingleProtectedDecl: spd.definition.private_part,
        lambda _: No(T.PrivatePart.entity),
    ))

    @langkit_property(return_type=T.DeclarativePart.entity)
    def declarative_region():
        """
        Return the (first) declarative region of this BasicDecl, if applicable.
        """
        return No(T.DeclarativePart.entity)

    aspects = AbstractField(type=T.AspectSpec, doc="""
        Return the list of aspects that are attached to this node.
    """)

    @langkit_property(return_type=T.AspectAssoc.entity, public=True)
    def get_aspect(name=Symbol):
        """
        Return the aspect with name ``name`` for this entity.
        """
        return Entity.aspects._.aspect_assocs.find(
            lambda asp: asp.aspect_name(asp.id) == name.image
        )

    @langkit_property(return_type=T.Expr.entity, public=True)
    def get_aspect_expr(name=Symbol):
        """
        Return the expression associated to the aspect with name ``name`` for
        this entity.
        """
        return Entity.get_aspect(name)._.expr

    @langkit_property()
    def library_item_pragmas():
        """
        If this entity is a library item, return the compilation unit pragmas.
        """
        return Cond(
            Entity.parent.is_a(T.LibraryItem),
            Entity.parent.parent.cast(T.CompilationUnit).pragmas,

            Entity.parent.parent.is_a(T.LibraryItem),
            Entity.parent.parent.parent.cast(T.CompilationUnit).pragmas,

            No(T.Pragma.list.entity)
        )

    @langkit_property(return_type=T.AdaNode.entity, public=True,
                      dynamic_vars=[default_imprecise_fallback()])
    def get_attribute(name=Symbol):
        """
        Return the attribute with name ``name`` associated to this entity.

        Attribute is the term chosen to unify data that can be set by the user
        on an Ada entity via three different mechanisms, sometimes
        interchangeably: Pragmas, aspects and representation clauses.

        This will return the syntactic node corresponding to attribute
        directly.
        """
        return (
            Entity.get_pragma(name).cast(T.AdaNode)
            ._or(Entity.get_aspect(name))
            ._or(Entity.get_representation_clause(name))
        )

    @langkit_property(return_type=T.Pragma.entity, public=True)
    def get_pragma(name=Symbol):
        """
        Return the pragma with name ``name`` associated to this entity.
        """
        def pragma_pred(d):
            """
            Predicate to check that ``d`` is the pragma we're looking for.
            """
            return d.cast(T.Pragma).then(lambda p: And(
                # Check pragma's name
                p.id.name_is(name),
                # Check that it's associated to self
                Not(p.associated_decls.find(lambda d: d == Entity)
                    .is_null),
                # Check that the pragma is after the decl
                (Self < p.node)
            ))

        # First look at library level pragmas if Self is a library item
        return Entity.library_item_pragmas.then(
            # Check pragma's name
            lambda plist: plist.find(lambda p: p.id.name_is(name)),
        )._or(
            # First look in the scope where Self is declared
            Entity.declarative_scope._.decls.as_entity.find(pragma_pred)

            # Then, if entity is declared in the public part of a package,
            # corresponding pragma might be in the private part.
            ._or(Entity.declarative_scope.cast(T.PublicPart).then(
                lambda pp: pp.parent.cast_or_raise(T.BasePackageDecl)
                .private_part._.decls.as_entity.find(pragma_pred)
            ))

            # Then, look inside decl, in the first declarative region of decl
            ._or(Entity.declarative_region._.decls.find(pragma_pred))

            .cast(T.Pragma)
        )

    @langkit_property(return_type=T.AttributeDefClause.entity, public=True,
                      dynamic_vars=[default_imprecise_fallback()])
    def get_representation_clause(name=Symbol):
        """
        Return the representation clause associated to this type decl that
        defines the given attribute name.
        """
        return Entity.declarative_scope._.decls.as_entity.find(
            lambda d: d.cast(T.AttributeDefClause).then(
                lambda p: Let(
                    lambda attr=p.attribute_expr.cast_or_raise(T.AttributeRef):
                        And(attr.attribute.name_is(name),
                            attr.prefix.referenced_decl == Entity)
                )
            )
        ).cast(T.AttributeDefClause.entity)

    @langkit_property(public=True)
    def is_unit_root():
        """
        Whether a BasicDecl is the root decl for its unit.
        """
        return Self.parent.then(lambda p: p.match(
            lambda _=T.LibraryItem: True,
            lambda gen_pkg_decl=T.GenericPackageDecl:
                gen_pkg_decl.parent.then(lambda p: p.is_a(LibraryItem)),
            lambda _=T.Subunit: True,
            lambda _: False,
        ))

    is_in_private_part = Property(Self.parent.parent.is_a(T.PrivatePart))

    @langkit_property(return_type=Bool)
    def subp_decl_match_signature(other=T.BasicDecl.entity):
        return (
            Entity.subp_spec_or_null.match_signature(
                other.subp_spec_or_null.cast_or_raise(T.SubpSpec),
                False
            )
        )

    @langkit_property(return_type=T.BasicDecl.entity.array, memoized=True)
    def base_subp_declarations():
        """
        If Self declares a primitive subprogram of some tagged type T, return
        the set of all subprogram declarations that it overrides (including
        itself).
        """
        return Entity.is_subprogram.then(
            lambda _: Entity.subp_spec_or_null.then(
                lambda spec: spec.primitive_subp_of_tagged.then(
                    lambda t:
                    t.primitives_env.get(Entity.name_symbol).filtermap(
                        lambda bd: bd.cast(BasicDecl),
                        lambda bd: bd.cast(BasicDecl)._.subp_spec_or_null.then(
                            lambda s:
                            # Since `s` is retrieved from `t`'s primitives env,
                            # its metadata fields `primitive` and
                            # `primitive_real_type` are set and therefore
                            # the following `match_signature` call will return
                            # true if `s` is overridable by `spec`.
                            s.match_signature(spec, match_name=False,
                                              use_entity_info=True)
                        )
                    )
                )
            )
        )

    @langkit_property(return_type=T.BasicDecl.entity.array, public=True,
                      dynamic_vars=[default_origin()])
    def root_subp_declarations():
        """
        If Self declares a primitive subprogram of some tagged type T, return
        the root subprogram declarations that it overrides. There can be
        several, as in the following scenario:

        - package Root defines the root tagged type T and subprogram Foo.
        - package Itf defines interface I and abstract subprogram Foo.
        - package D defines "type U is new Root.T and Itf.I" and an overriding
          subprogram Foo.

        Here, root_subp_declarations of Foo defined in package D will return
        both Foo from package Root and Foo from package Itf.
        """
        # Get all the parent overrides defined for this subprogram. That is,
        # if this subprogram is a primitive of some type T and overrides some
        # subprogram P, get all the other overrides of P which are primitives
        # of parent types of T.
        base_decls = Var(Entity.base_subp_declarations)

        # Compute the set of all such types for which an override is declared
        base_types = Var(base_decls.map(
            lambda d: d.info.md.primitive.cast(BaseTypeDecl).as_bare_entity
        ))

        # Among this set of type, find the one which is not derived from any
        # of the other, i.e. the base-most type on which the original
        # subprogram is declared.
        return base_types.filter(
            lambda t: Not(base_types.any(
                lambda u: And(
                    t != u,
                    t.is_derived_type(u)
                )
            ))
        ).map(
            # Get back the subprogram
            lambda root_type: base_decls.find(
                lambda d: d.info.md.primitive == root_type.node
            )
        )

    annotations = Annotations(custom_short_image=True)

    defining_names = AbstractProperty(
        type=T.DefiningName.entity.array, public=True,
        doc="""
        Get all the names of this basic declaration.
        """
    )

    defining_name = Property(
        Entity.defining_names.at(0), public=True, ignore_warn_on_node=True,
        doc="""
        Get the name of this declaration. If this declaration has several
        names, it will return the first one.
        """
    )

    defining_env = Property(
        EmptyEnv,
        dynamic_vars=[origin],
        doc="""
        Return a lexical environment that contains entities that are accessible
        as suffixes when Self is a prefix.
        """
    )

    @langkit_property(dynamic_vars=[origin], return_type=T.BaseTypeDecl.entity)
    def identity_type():
        return Entity.match(
            lambda _=T.ExceptionDecl: Self.exc_id_type,
            lambda _=T.TaskTypeDecl: Self.task_id_type,
            lambda _=T.TaskBody: Self.task_id_type,
            lambda _: No(T.BaseTypeDecl.entity)
        )

    @langkit_property(dynamic_vars=[origin], return_type=Int)
    def array_ndims():
        return Entity.expr_type.array_ndims

    is_array = Property(Entity.array_ndims > 0, dynamic_vars=[origin])

    @langkit_property(return_type=T.BaseTypeDecl.entity,
                      dynamic_vars=[origin])
    def expr_type():
        """
        Return the type declaration corresponding to this basic declaration
        has when it is used in an expression context. For example, for this
        basic declaration::

            type Int is range 0 .. 100;

            A : Int := 12;

        the declaration of the Int type will be returned. For this
        declaration::

            type F is delta 0.01 digits 10;

            function B return F;

        expr_type will return the declaration of the type F.
        """
        ret = Var(Entity.type_expression.then(lambda te: te.designated_type))

        # If the entity is actually an anonymous access to the decl rather than
        # the decl itself, return an anonymous access type pointing to the type
        # of the decl.
        ret_2 = Var(If(
            Entity.info.md.access_entity,
            ret.anonymous_access_type,
            ret
        ))

        return ret_2

    type_expression = Property(
        No(T.TypeExpr).as_entity,
        type=T.TypeExpr.entity,
        public=True,
        doc="""
        Return the type expression for this BasicDecl if applicable, a null
        otherwise.
        """
    )

    @langkit_property(return_type=T.BaseSubpSpec.entity, public=True)
    def subp_spec_or_null(follow_generic=(Bool, False)):
        """
        If Self is a Subp, returns the specification of this subprogram.

        If ``follow_generic`` is True, will also work for instances of
        ``GenericSubpDecl``.
        """
        return Entity.match(
            lambda subp=BasicSubpDecl:  subp.subp_decl_spec,
            lambda subp=BaseSubpBody:   subp.subp_spec,
            lambda subp=SubpBodyStub:   subp.subp_spec,
            lambda entry=EntryDecl:     entry.spec,
            lambda gsp=GenericSubpDecl:
            If(follow_generic, gsp.subp_decl.subp_spec, No(SubpSpec.entity)),
            lambda _:                   No(SubpSpec.entity),
        )

    @langkit_property(return_type=T.BaseFormalParamHolder.entity)
    def formal_param_holder_or_null():
        return Entity.match(
            lambda t=T.TypeDecl: t.discriminants,
            lambda _: Entity.subp_spec_or_null(True)
        )

    @langkit_property(return_type=Bool, public=True)
    def is_subprogram():
        """
        Return True if self is a subprogram node in the general sense (which
        is, an entity that can be called). This includes separates and entries.
        """
        return Self.is_a(BasicSubpDecl, BaseSubpBody, SubpBodyStub, EntryDecl)

    @langkit_property(return_type=Bool)
    def is_stream_subprogram_for_type(typ=T.BaseTypeDecl.entity,
                                      return_obj=Bool):
        root_stream_type = Var(
            Entity
            .get_unit_root_decl(['Ada', 'Streams'], UnitSpecification)
            ._.children_env
            .get_first('Root_Stream_Type', lookup=LK.flat)
            .cast(T.BaseTypeDecl).classwide_type.cast(T.BaseTypeDecl)
        )
        params = Var(Entity.subp_spec_or_null._.unpacked_formal_params)

        return bind_origin(
            Self,
            Self.is_subprogram
            & params.at(0).spec.formal_type.is_access_to(root_stream_type)
            & If(
                return_obj,
                Entity.subp_spec_or_null.return_type.matching_formal_type(typ),
                params.at(1).spec.formal_type.matching_formal_type(typ),
            )
        )

    @langkit_property(return_type=Bool)
    def can_be_paramless():
        """
        Return true if entity can be a paramless subprogram entity, when used
        in an expression context.
        """
        return Entity.subp_spec_or_null.then(
            lambda ss: ss.paramless(
                Entity.info.md.dottable_subp, can_be=True
            ),
            default_val=True
        )

    @langkit_property(return_type=Bool)
    def is_paramless():
        """
        Return true if entity is a paramless subprogram entity, when used
        in an expression context.
        """
        return Entity.subp_spec_or_null.then(
            lambda ss: ss.paramless(
                Entity.info.md.dottable_subp, can_be=False
            ),
            default_val=True
        )

    @langkit_property(return_type=Equation, dynamic_vars=[origin])
    def constrain_prefix(prefix=T.Expr):
        """
        This method is used when self is a candidate suffix in a dotted
        expression, to express the potential constraint that the suffix could
        express on the prefix.

        For example, given this code::

            1 type P is record
            2     A, B : Integer;
            3 end record;
            4
            5 P_Inst : P;
            7
            8 P_Inst.A;
              ^^^^^^^^

        A references the A ComponentDecl at line 2, and the constraint that we
        want to express on the prefix (P_Inst), is that it needs to be of type
        P.
        """
        # Default implementation returns logic true => does not add any
        # constraint to the xref equation.
        ignore(prefix)
        return LogicTrue()

    relative_name = Property(
        Entity.defining_name.relative_name, public=True, doc="""
        Return the relative name for Self. If Self's defining name is
        ``A.B.C``, return C as a node.
        """
    )

    relative_name_text = Property(
        Entity.relative_name.symbol, doc="""
        Return the relative name for Self, as text.
        """, public=True
    )

    name_symbol = Property(Self.as_bare_entity.relative_name.symbol)

    @langkit_property()
    def basic_decl_next_part_for_decl():
        """
        Implementation of next_part_for_decl for basic decls, that can be
        reused by subclasses when they override next_part_for_decl.
        """
        ignore(Var(Self.body_unit))

        return If(
            Self.is_a(T.GenericSubpInternal), Entity.parent.children_env,
            Entity.children_env
        ).get_first('__nextpart', lookup=LK.flat,
                    categories=noprims).cast(T.BasicDecl)

    @langkit_property(public=True)
    def next_part_for_decl():
        """
        Return the next part of this declaration, if applicable.

        .. note:: It is not named next_part, because BaseTypeDecl has a
            more precise version of next_part that returns a BaseTypeDecl.
            Probably, we want to rename the specific versions, and have the
            root property be named next_part. (TODO R925-008)
        """
        return Entity.basic_decl_next_part_for_decl

    @langkit_property(public=True)
    def body_part_for_decl():
        """
        Return the body corresponding to this declaration, if applicable.

        .. note:: It is not named body_part, subclasses have more precise
            versions named body_part and returning a more precise result.
            Probably, we want to rename the specific versions, and have the
            root property be named previous_part. (TODO R925-008)
        """
        return Entity.next_part_for_decl.then(lambda np: np.match(
            lambda stub=BodyStub: stub.next_part_for_decl,
            lambda other: other
        )).cast(T.Body)

    @langkit_property(dynamic_vars=[env])
    def decl_scope(follow_private=(Bool, True)):
        scope = Var(Entity.defining_name.parent_scope)

        # If this the corresponding decl is a generic, go grab the internal
        # package decl. Then If the package has a private part, then get the
        # private part, else return the public part.
        return Let(
            lambda public_scope=scope.env_node.cast(T.GenericPackageDecl).then(
                lambda gen_pkg_decl: If(
                    Self.is_a(FormalSubpDecl),
                    scope,
                    gen_pkg_decl.package_decl.children_env,
                ),
                default_val=scope
            ): If(
                And(
                    follow_private,
                    public_scope.env_node._.is_a(
                        T.BasePackageDecl, T.SingleProtectedDecl,
                        T.ProtectedTypeDecl
                    )
                ),

                # Don't try to go to private part if we're not in a package
                # decl.

                public_scope.get('__privatepart', lookup=LK.flat).at(0).then(
                    lambda pp: pp.children_env, default_val=public_scope
                ),
                public_scope
            )
        )

    @langkit_property(public=True, return_type=Symbol.array)
    def fully_qualified_name_array():
        """
        Return the fully qualified name corresponding to this declaration, as
        an array of symbols.
        """
        ent = Var(Self.as_bare_entity)
        fqn = Var(If(
            ent.is_unit_root,
            ent.defining_name.as_symbol_array,

            ent.semantic_parent.cast(T.BasicDecl)
            ._.fully_qualified_name_array.then(lambda fqn: If(
                Self.is_a(T.GenericPackageInternal),
                fqn,
                fqn.concat(ent.defining_name._.as_symbol_array)
            ))
        ))

        return Self.parent.cast(
            T.Subunit)._.name.as_symbol_array.concat(fqn)._or(fqn)

    @langkit_property(public=True, return_type=T.String)
    def fully_qualified_name():
        """
        Return the fully qualified name corresponding to this declaration.
        """
        return Entity.sym_join(Entity.fully_qualified_name_array, String("."))

    @langkit_property(public=True, return_type=T.String)
    def unique_identifying_name():
        """
        Return a unique identifying name for this declaration. In the case of
        subprograms, this will include the profile.
        """
        return Entity.match(
            lambda _=T.AnonymousTypeDecl: Entity.custom_id_text,
            lambda _: Entity.fully_qualified_name.concat(Entity.custom_id_text)
        )

    @langkit_property(return_type=T.String)
    def custom_id_text():
        return Entity.subp_spec_or_null.then(
            # For subprograms, we'll compute their profiles as the unique
            # identifying text.
            lambda ss: ss.unpacked_formal_params.then(
                lambda ufp: String(" (").concat(
                    Entity.string_join(
                        ufp.map(
                            lambda p: p.spec.type_expression.custom_id_text
                        ),
                        String(", ")
                    )
                ).concat(String(")"))
            ).concat(ss.returns.then(
                lambda r: String(" return ").concat(r.custom_id_text)
            )),
            default_val=String("")
        )


class ErrorDecl(BasicDecl):
    """
    Placeholder node for syntax errors in lists of declarations.
    """
    aspects = NullField()
    defining_names = Property(No(T.DefiningName.entity.array))


@abstract
class Body(BasicDecl):
    """
    Base class for an Ada body (RM 3.11). A body is the completion of a
    declaration.
    """

    @langkit_property(return_type=Bool, dynamic_vars=[origin])
    def in_scope():
        """
        Return True if ``origin`` is directly in the scope of this body.
        """
        return And(
            origin.unit == Self.unit,
            Not(origin.parents.find(lambda p: p == Self).is_null)
        )

    @langkit_property()
    def body_decl_scope():
        """
        Return the scope of this body's decl.
        """
        return env.bind(
            Self.initial_env,
            Entity.body_scope(True, True)
        )

    @langkit_property()
    def subp_previous_part():
        """
        Return the decl corresponding to this body. Specialized implementation
        for subprogram bodies.
        """
        return If(
            Self.is_unit_root & Not(Self.is_subunit),

            # If library item, we just return the spec. We don't check if it's
            # a valid and matching subprogram because that's an error case.
            Self.top_level_decl(Self.spec_unit).as_entity,

            # If not a library item, find the matching subprogram spec in the
            # env.
            Entity.children_env.env_parent.get(Entity.name_symbol)
            .find(lambda sp: And(Not(sp.is_null),
                  Not(sp.node == Self),
                  sp.match(
                      # If this body completes a generic subprogram, then we
                      # just return it (no need to match the signature).
                      lambda _=T.GenericSubpDecl: True,

                      lambda subp_decl=T.BasicSubpDecl:
                      subp_decl.subp_decl_spec.match_signature(
                          Entity.subp_spec_or_null.cast(T.SubpSpec), True,
                          # We set use_entity_info to False so as to not match
                          # base subprograms.
                          use_entity_info=False
                      ),

                      lambda subp_stub=T.SubpBodyStub:
                      subp_stub.subp_spec.match_signature(
                          Entity.subp_spec_or_null.cast(T.SubpSpec), True,
                          # We set use_entity_info to False so as to not match
                          # base subprograms.
                          use_entity_info=False
                      ),

                      lambda _: False
            ))).cast_or_raise(T.BasicDecl.entity)
        )

    @langkit_property(dynamic_vars=[env])
    def package_previous_part():
        """
        Return the BasePackageDecl corresponding to this node.

        If the case of generic package declarations, this returns the
        ``package_decl`` field instead of the ``GenericPackageDecl`` itself.
        """
        return bind_origin(
            Self, Entity.defining_name.all_env_els_impl
        ).at(0)._.match(
            lambda pkg_decl=T.PackageDecl: pkg_decl,
            lambda gen_pkg_decl=T.GenericPackageDecl:
                gen_pkg_decl.package_decl,
            lambda _: No(T.BasicDecl.entity)
        )

    @langkit_property(dynamic_vars=[env])
    def protected_previous_part():
        """
        Return the ProtectedDef corresponding to this node.
        """
        return Entity.defining_name.env_elements.at(0)._.match(
            lambda prot_decl=T.ProtectedTypeDecl: prot_decl,
            lambda _: No(T.BasicDecl.entity)
        )

    @langkit_property(public=True, return_type=T.BasicDecl.entity)
    def previous_part():
        """
        Return the previous part for this body. Might be a declaration or a
        body stub.
        """
        return env.bind(Self.node_env, Entity.match(
            lambda _=T.BaseSubpBody: Entity.subp_previous_part,
            lambda _=T.PackageBody: Entity.package_previous_part,
            lambda _=T.PackageBodyStub: Entity.package_previous_part,
            lambda _=T.ProtectedBody: Entity.protected_previous_part,
            lambda _=T.ProtectedBodyStub: Entity.protected_previous_part,
            lambda _=T.TaskBody: bind_origin(
                Self, Entity.defining_name.all_env_els_impl
            ).at(0).cast(T.BasicDecl),
            lambda _: No(T.BasicDecl.entity),
        ))

    @langkit_property(public=True)
    def decl_part():
        """
        Return the decl corresponding to this node if applicable.
        """
        return Entity.previous_part.then(lambda prev_part: prev_part.match(
            # Stubs have one more previous part. Go back one more level to get
            # the decl.
            lambda stub=T.BodyStub: stub.previous_part,
            lambda other: other
        ))

    @langkit_property()
    def is_subunit():
        return Not(Self.parent.cast(T.Subunit).is_null)

    @langkit_property(ignore_warn_on_node=True, public=True)
    def subunit_root():
        """
        If self is a subunit, return the body in which it is rooted.
        """
        return Self.parent.cast(T.Subunit).then(lambda su: Self.top_level_decl(
            su.name.referenced_unit_or_null(UnitBody)
        ))

    @langkit_property(dynamic_vars=[env])
    def body_scope(follow_private=Bool, force_decl=(Bool, False)):
        """
        Return the scope for this body.
        If follow_private, then returns the private part if possible.

        If force_decl, then returns the corresponding declaration's scope,
        rather than the parent body's scope.
        """

        scope = Var(Cond(
            # Subunits always appear at the top-level in package bodies. So if
            # this is a subunit, the scope is the same as the scope of the
            # corresponding "is separate" decl, hence: the defining env of this
            # top-level package body.
            Not(Self.subunit_root.is_null), Self.subunit_root.children_env,

            # In case this is a library level subprogram that has no spec
            # (which is legal), we'll register this body in the parent
            # scope.
            Self.is_subprogram & Self.is_unit_root,
            Let(lambda dns=Entity.defining_name.scope:
                # If the scope is self's scope, return parent scope, or
                # else we'll have an infinite recursion.
                If(dns.is_null | (dns.env_node == Self),
                   Entity.defining_name.parent_scope,
                   dns)),

            # If this is a library level unit, or force_decl is True, return
            # the enclosing decl.
            Self.is_unit_root | force_decl, Entity.defining_name.scope,

            # The rest of cases are nested declarations: In that case we want
            # to take the parent's env.
            Self.parent.children_env,
        ))

        # If this the corresponding decl is a generic, go grab the internal
        # package decl.
        public_scope = Var(scope.env_node.cast(T.GenericPackageDecl).then(
            lambda gen_pkg_decl: gen_pkg_decl.package_decl.children_env,
            default_val=scope
        ))

        # If the package has a private part, then get the private part,
        # else return the public part.
        return If(
            And(
                follow_private,
                public_scope.env_node._.is_a(
                    T.BasePackageDecl, T.SingleProtectedDecl,
                    T.ProtectedTypeDecl,
                )
            ),
            public_scope.get('__privatepart', lookup=LK.flat).at(0).then(
                lambda pp: pp.children_env, default_val=public_scope
            ),
            public_scope
        )


@abstract
class BodyStub(Body):
    """
    Base class for a body stub (RM 10.1.3). A body stub is meant to be
    completed by .
    """
    pass


@abstract
class BaseFormalParamDecl(BasicDecl):
    """
    Base class for formal parameter declarations. This is used both for records
    components and for subprogram parameters.

    This is a Libadalang abstaction, that has no ARM existence.
    """
    is_mandatory = Property(False)

    formal_type = Property(
        bind_origin(Self, Entity.type_expression._.designated_type),
        doc="Return the type for this formal.", public=True
    )

    @langkit_property()
    def parent_decl():
        return Entity.semantic_parent.cast(T.BasicDecl)

    @langkit_property(return_type=T.DefiningName.entity)
    def get_param(part=T.BasicDecl.entity,
                  param=(T.DefiningName.entity, No(T.DefiningName.entity))):

        p = Var(param._or(Entity.defining_name))

        return part.then(lambda d: (
            d.formal_param_holder_or_null._.unpacked_formal_params
            .find(lambda sf: sf.name.name_is(p.name_symbol)).name
        ))

    @langkit_property(return_type=T.DefiningName.entity)
    def decl_param(param=T.DefiningName.entity):
        """
        If self is a ParamSpec of a subprogram body, go fetch the equivalent
        spec in the subprogram decl.
        """
        return Entity.get_param(
            Entity.parent_decl.cast(T.BaseSubpBody)._.decl_part, param
        )._or(param)

    @langkit_property()
    def next_part_for_decl():
        return Entity.get_param(
            Entity.parent_decl._.next_part_for_decl)._.basic_decl

    @langkit_property()
    def previous_part_for_decl():
        return Entity.get_param(
            Entity.parent_decl._.previous_part_for_decl)._.basic_decl


class DiscriminantSpec(BaseFormalParamDecl):
    """
    Known list of discriminants in type declarations.
    """
    ids = Field(type=T.DefiningName.list)
    type_expr = Field(type=T.TypeExpr)
    default_expr = Field(type=T.Expr)
    aspects = NullField()

    env_spec = EnvSpec(add_to_env(env_mappings(Self.ids, Self)))

    defining_names = Property(Self.ids.map(lambda id: id.as_entity))
    defining_env = Property(Entity.type_expr.defining_env)

    type_expression = Property(Entity.type_expr)

    xref_entry_point = Property(True)

    @langkit_property()
    def xref_equation():
        return And(
            Entity.type_expr.sub_equation,
            Entity.default_expr.then(
                lambda de:
                de.sub_equation
                & Bind(de.node.type_var,
                       Entity.expr_type,
                       eq_prop=BaseTypeDecl.matching_assign_type),
                default_val=LogicTrue()
            )
        )


@abstract
class BaseFormalParamHolder(AdaNode):
    """
    Base class for lists of formal parameters. This is used both for subprogram
    specifications and for records, so that we can share the matching and
    unpacking logic.
    """

    abstract_formal_params = AbstractProperty(
        type=BaseFormalParamDecl.entity.array,
        doc="Return the list of abstract formal parameters for this holder.",
        public=True
    )

    unpacked_formal_params = Property(
        Self.unpack_formals(Entity.abstract_formal_params),
        doc="""
        Couples (identifier, param spec) for all parameters
        """
    )

    @langkit_property(return_type=T.ParamMatch.array, dynamic_vars=[env])
    def match_param_list(params=T.AssocList.entity,
                         is_dottable_subp=Bool):
        return Self.match_formals(
            Entity.abstract_formal_params, params, is_dottable_subp
        )

    nb_min_params = Property(
        Self.as_bare_entity.unpacked_formal_params.filter(
            lambda p: p.spec.is_mandatory
        ).length,
        type=Int, public=True, doc="""
        Return the minimum number of parameters this subprogram can be called
        while still being a legal call.
        """
    )

    nb_max_params = Property(
        Self.as_bare_entity.unpacked_formal_params.length, public=True,
        type=Int, doc="""
        Return the maximum number of parameters this subprogram can be called
        while still being a legal call.
        """
    )

    @langkit_property(return_type=Bool)
    def paramless(dottable_subp=Bool, can_be=(Bool, True)):
        """
        Utility function. Given a subprogram spec and whether the subprogram
        was referenced using the dot notation, determine if it can be called
        without parameters (and hence without a callexpr).
        """
        nb_params = Var(If(can_be, Self.nb_min_params, Self.nb_max_params))
        return Or(
            dottable_subp & (nb_params == 1),
            nb_params == 0
        )

    @langkit_property(return_type=Bool, dynamic_vars=[env])
    def is_matching_param_list(params=T.AssocList.entity,
                               is_dottable_subp=Bool):
        """
        Return whether a AssocList is a match for this SubpSpec, i.e.
        whether the argument count (and designators, if any) match.
        """
        bare = Var(Self.as_bare_entity)
        match_list = Var(bare.match_param_list(params, is_dottable_subp))
        nb_max_params = If(is_dottable_subp, bare.nb_max_params - 1,
                           bare.nb_max_params)
        nb_min_params = If(is_dottable_subp, bare.nb_min_params - 1,
                           bare.nb_min_params)

        return And(
            params.length <= nb_max_params,
            match_list.all(lambda m: m.has_matched),
            match_list.filter(
                lambda m: m.formal.spec.is_mandatory
            ).length == nb_min_params,
        )


@abstract
class DiscriminantPart(BaseFormalParamHolder):
    """
    Specification for discriminants in type declarations.
    """

    @langkit_property()
    def abstract_formal_params():
        return No(T.BaseFormalParamDecl.entity.array)


class KnownDiscriminantPart(DiscriminantPart):
    """
    Known list of discriminants in type declarations.
    """

    discr_specs = Field(type=T.DiscriminantSpec.list)

    @langkit_property()
    def abstract_formal_params():
        return Self.discr_specs.map(
            lambda e: e.cast(BaseFormalParamDecl).as_entity
        )


class UnknownDiscriminantPart(DiscriminantPart):
    """
    Unknown list of discriminants in type declarations.
    """
    pass


@abstract
class TypeDef(AdaNode):
    """
    Base class for type definitions.
    """

    @langkit_property(return_type=T.DiscreteRange)
    def discrete_range():
        """
        Return the discrete range for this type def, if applicable.
        """
        return No(DiscreteRange)

    array_ndims = Property(
        Literal(0),
        doc="""
        If this designates an array type, return its number of dimensions.
        Return 0 otherwise.
        """,
        dynamic_vars=[origin]
    )

    is_real_type = Property(
        Entity.is_float_type | Entity.is_fixed_point,
        doc="Whether type is a real type or not."
    )

    is_float_type = Property(False, doc="Whether type is a float type or not.")
    is_fixed_point = Property(False,
                              doc="Whether type is a fixed point type or not.")

    @langkit_property(dynamic_vars=[origin])
    def is_discrete_type():
        return Entity.is_int_type | Entity.is_enum_type | Entity.is_char_type

    @langkit_property(dynamic_vars=[origin])
    def is_int_type():
        """Whether type is an integer type or not."""
        return False

    is_access_type = Property(
        False, uses_entity_info=False,
        doc="Whether type is an access type or not.",
        dynamic_vars=[origin]
    )

    is_char_type = Property(False)
    is_enum_type = Property(False)
    is_record_type = Property(False)

    @langkit_property(dynamic_vars=[origin])
    def accessed_type():
        return No(BaseTypeDecl.entity)

    @langkit_property(public=True)
    def is_tagged_type():
        """
        Return whether this type is tagged.
        """
        return False

    base_type = Property(
        No(T.BaseTypeDecl.entity), doc="""
        Return the base type entity for this derived type definition.
        """
    )

    base_types = Property(
        Entity.base_type.then(lambda bt: bt.singleton)
        .concat(Entity.base_interfaces),
        doc="""
        Return all the base types for this type (base type + base interfaces)
        """
    )

    base_interfaces = Property(
        No(T.BaseTypeDecl.entity.array), doc="""
        Return the interfaces this type derives from
        """
    )

    @langkit_property(dynamic_vars=[origin])
    def defining_env():
        # Regroup implementations for subclasses here instead of overriding to
        # avoid code duplication (multiple cases have the same implementation).
        return Cond(
            # A "record" or "private" type def may be the completion of a
            # previous type declaration, so we need to include the defining
            # env of its previous part as well.
            Self.is_a(T.RecordTypeDef, T.PrivateTypeDef),
            Array([Entity.children_env, Entity.previous_part_env]).env_group(),

            # Same for "derived" and "interface" type definitions, but we also
            # need to include the defining environments of their base types.
            Self.is_a(T.DerivedTypeDef, T.InterfaceTypeDef),
            Entity.base_types.map(
                lambda bt: bt._.defining_env
            ).concat(
                Array([Entity.children_env, Entity.previous_part_env])
            ).env_group(),

            Entity.match(
                lambda ar=T.ArrayTypeDef: ar.comp_type.defining_env,

                # An access to procedure will have a null accessed_type, hence
                # the use of the underscore.
                lambda ac=T.AccessDef: ac.accessed_type._.defining_env,

                lambda _: EmptyEnv
            )
        )

    containing_type = Property(
        Entity.parent.cast_or_raise(T.TypeDecl), doc="""
        Return the TypeDecl containing this TypeDef
        """
    )

    previous_part = Property(Entity.containing_type.previous_part(True))

    previous_part_env = Property(
        Entity.previous_part._.defining_env,
        dynamic_vars=[origin]
    )

    is_static = Property(False, dynamic_vars=[default_imprecise_fallback()])


class Variant(AdaNode):
    """
    Single variant in a discriminated type record declaration.

    This corresponds to a ``when ... => ...`` section in a variant part.
    """
    choices = Field(type=T.AlternativesList)
    components = Field(type=T.ComponentList)

    @langkit_property(return_type=Bool)
    def choice_match(choice=T.AdaNode.entity, val=T.BigInt):
        """
        Checks whether val matches choice.
        """
        return choice.match(

            # If choice is a binop, it is either a range, or a static
            # arithmetic expression.
            lambda bo=T.BinOp: If(
                # If choice is a range, then check that val is in the range
                bo.op.is_a(Op.alt_double_dot),

                And(val >= bo.left.eval_as_int,
                    val <= bo.right.eval_as_int),

                val == bo.eval_as_int,
            ),

            # If choice is a name, it is either a subtype name, either a
            # constant number name.
            lambda n=T.Name: n.name_designated_type._.discrete_range.then(
                lambda dr: And(val >= dr.low_bound, val <= dr.high_bound),
                default_val=(val == n.eval_as_int)
            ),

            # If choice is a subtype indication, then get the range
            lambda st=T.SubtypeIndication: st.discrete_range.then(
                lambda dr: And(val >= dr.low_bound,
                               val <= dr.high_bound)
            ),

            # If it is an expr, then just check for equality
            lambda e=T.Expr: val == e.eval_as_int,

            # If 'others', always return true
            lambda _=T.OthersDesignator: True,

            lambda _: False,
        )

    @langkit_property(return_type=Bool)
    def matches(expr=T.Expr.entity):
        """
        Check if any choice in the choice list matches expr's value.
        """
        # Statically evaluate expr
        expr_val = Var(expr.eval_as_int)

        return Entity.choices.any(
            lambda c: Self.choice_match(c, expr_val)
        )


class VariantPart(AdaNode):
    """
    Variant part in a discriminated type record declaration.

    This corresponds to the whole ``case ... is ... end case;`` block.
    """
    discr_name = Field(type=T.Identifier)
    variant = Field(type=T.Variant.list)

    xref_entry_point = Property(True)

    @langkit_property()
    def xref_equation():
        ignore(Var(
            Entity.discr_name.resolve_names_internal(True, LogicTrue()))
        )

        return Entity.variant.logic_all(lambda var: (
            var.choices.logic_all(lambda c: c.match(
                # Expression case
                lambda e=T.Expr:
                TypeBind(e.type_var, Self.discr_name.type_val)
                & e.sub_equation,

                # TODO: Bind other cases: SubtypeIndication and Range
                lambda _: LogicTrue()
            ))
        ))

    @langkit_property(return_type=T.BaseFormalParamDecl.entity.array)
    def get_components(discriminants=T.ParamMatch.array):
        """
        Get components for this variant part, depending on the values of
        discriminants.
        """
        # Get the specific discriminant this variant part depends upon
        discr = Var(discriminants.find(
            lambda d: d.formal.name.name_is(Self.discr_name.symbol)
        ))

        # Get the variant branch with a choice that matches the discriminant's
        # value.
        variant = Var(Entity.variant.find(
            lambda v: v.matches(discr.actual.assoc.expr)
        ))

        # Get the components for this variant branch. We're passing down
        # discriminants, because there might be a nested variant part in this
        # variant branch.
        return variant.components.abstract_formal_params_impl(
            discriminants, False, False
        )


class ComponentDecl(BaseFormalParamDecl):
    """
    Declaration for a component.
    """
    ids = Field(type=T.DefiningName.list)
    component_def = Field(type=T.ComponentDef)
    default_expr = Field(type=T.Expr)
    aspects = Field(type=T.AspectSpec)

    env_spec = EnvSpec(add_to_env(env_mappings(Self.ids, Self)))

    defining_env = Property(
        Entity.component_def.type_expr.defining_env,
        doc="See BasicDecl.defining_env"
    )

    defining_names = Property(Self.ids.map(lambda i: i.as_entity))

    array_ndims = Property(Entity.component_def.type_expr.array_ndims)

    type_expression = Property(Self.component_def.type_expr.as_entity)

    @langkit_property(return_type=Equation)
    def constrain_prefix(prefix=T.Expr):
        # Simple type equivalence
        return Bind(prefix.type_var,
                    Entity.container_type,
                    eq_prop=BaseTypeDecl.matching_prefix_type)

    @langkit_property(return_type=T.BaseTypeDecl.entity)
    def container_type():
        """
        Return the defining container type for this component declaration.
        """
        return Self.parents.find(
            lambda p: p.is_a(BaseTypeDecl)
        ).cast(BaseTypeDecl).as_entity

    @langkit_property()
    def xref_equation():
        typ = Var(Entity.expr_type)
        return (
            Entity.component_def.type_expr.sub_equation
            & Entity.default_expr.then(
                lambda de:
                de.sub_equation
                & Bind(de.type_var, typ,
                       eq_prop=BaseTypeDecl.matching_assign_type),
                default_val=LogicTrue()
            )
        )

    xref_entry_point = Property(True)


class ComponentList(BaseFormalParamHolder):
    """
    List of component declarations.
    """
    components = Field(type=T.AdaNode.list)
    variant_part = Field(type=T.VariantPart)

    type_def = Property(Self.parent.parent.cast(T.TypeDef).as_entity)
    type_decl = Property(Entity.type_def.parent.cast(T.TypeDecl))

    parent_component_list = Property(origin.bind(
        Self,
        Entity.type_def.cast(T.DerivedTypeDef)._.base_type.record_def._.comps
    ))

    @langkit_property(return_type=BaseFormalParamDecl.entity.array,
                      dynamic_vars=[env, default_origin()])
    def abstract_formal_params_for_assocs(assocs=T.AssocList.entity):

        td = Var(Entity.type_decl)
        discriminants = Var(td.discriminants_list)

        # Get param matches for discriminants only
        discriminants_matches = Var(Self.match_formals(
            td.discriminants_list, assocs, False
        ).filter(
            lambda pm: Not(discriminants
                           .find(lambda d: d == pm.formal.spec)
                           .is_null)
        ))

        # We run resolution for discriminants, because need ref and type
        # information to statically evaluate their values.
        ignore(Var(discriminants_matches.map(
            lambda match: match.actual.assoc.expr.resolve_names_internal(
                True, And(
                    TypeBind(match.actual.assoc.expr.type_var,
                             match.formal.spec
                             .type_expression.designated_type),
                    If(match.actual.name.is_null,
                       LogicTrue(),
                       Bind(match.actual.name.ref_var, match.formal.spec))
                )
            )
            # Explicitly raise an error if resolution of discriminants failed
            ._or(PropertyError(Bool, "Failure in discriminants' resolution"))
        )))

        # Get param matches for all aggregates' params. Here, we use and pass
        # down the discriminant matches, so that abstract_formal_params_impl is
        # able to calculate the list of components belonging to variant parts,
        # depending on the static value of discriminants.
        return td.record_def.comps.abstract_formal_params_impl(
            discriminants=discriminants_matches
        )

    @langkit_property(return_type=BaseFormalParamDecl.entity.array)
    def abstract_formal_params_impl(
        discriminants=T.ParamMatch.array,
        include_discriminants=(Bool, True),
        recurse=(Bool, True)
    ):

        # Get self's components. We pass along discriminants, to get variant
        # part's components too.
        self_comps = Var(Entity.components.keep(BaseFormalParamDecl).concat(
            Entity.variant_part._.get_components(discriminants)
        ))

        # Append parent's components.
        # TODO: The parent could have a variant part too, using the explicit
        # discriminants mappings. We need to handle that too.
        ret = Var(If(
            recurse,
            Entity.parent_component_list.then(
                lambda pcl: pcl.abstract_formal_params_impl(
                    No(T.ParamMatch.array), False
                )
                .concat(self_comps),
                default_val=self_comps
            ),
            self_comps
        ))

        return If(
            include_discriminants,
            Entity.type_decl._.discriminants_list.concat(ret),
            ret
        )

    @langkit_property()
    def abstract_formal_params():
        return Entity.abstract_formal_params_impl(No(T.ParamMatch.array))


@abstract
class BaseRecordDef(AdaNode):
    """
    Base class for record definitions.
    """
    components = Field(type=T.ComponentList)

    # TODO: Kludge, to remove when Q619-018 is implemented
    comps = Property(Entity.components)


class RecordDef(BaseRecordDef):
    """
    Record definition that contains components (``record ... end record``).
    """
    pass


class NullRecordDef(BaseRecordDef):
    """
    Record definition for ``null record``.
    """
    pass


class Tagged(AdaNode):
    """
    Qualifier for the ``tagged`` keyword.
    """
    enum_node = True
    qualifier = True


class Abstract(AdaNode):
    """
    Qualifier for the ``abstract`` keyword.
    """
    enum_node = True
    qualifier = True


class Limited(AdaNode):
    """
    Qualifier for the ``limited`` keyword.
    """
    enum_node = True
    qualifier = True


class Private(AdaNode):
    """
    Qualifier for the ``private`` keyword.
    """
    enum_node = True
    qualifier = True


class Aliased(AdaNode):
    """
    Qualifier for the ``aliased`` keyword.
    """
    enum_node = True
    qualifier = True


class NotNull(AdaNode):
    """
    Qualifier for the ``not null`` keywords.
    """
    enum_node = True
    qualifier = True


class Constant(AdaNode):
    """
    Qualifier for the ``constant`` keyword.
    """
    enum_node = True
    qualifier = True


class All(AdaNode):
    """
    Qualifier for the ``all`` keyword.
    """
    enum_node = True
    qualifier = True


class Abort(AdaNode):
    """
    Qualifier for the ``abort`` keyword.
    """
    enum_node = True
    qualifier = True


class Reverse(AdaNode):
    """
    Qualifier for the ``reverse`` keyword.
    """
    enum_node = True
    qualifier = True


class WithPrivate(AdaNode):
    """
    Qualifier for the ``private`` keyword in ``with private`` record clauses.
    """
    enum_node = True
    qualifier = True


class Until(AdaNode):
    """
    Qualifier for the ``until`` keyword.
    """
    enum_node = True
    qualifier = True


class Synchronized(AdaNode):
    """
    Qualifier for the ``synchronized`` keyword.
    """
    enum_node = True
    qualifier = True


class Protected(AdaNode):
    """
    Qualifier for the ``protected`` keyword.
    """
    enum_node = True
    qualifier = True


class RecordTypeDef(TypeDef):
    """
    Type definition for a record.
    """
    has_abstract = Field(type=Abstract)
    has_tagged = Field(type=Tagged)
    has_limited = Field(type=Limited)
    record_def = Field(type=T.BaseRecordDef)

    is_tagged_type = Property(Self.has_tagged.as_bool)
    is_record_type = Property(True)

    xref_equation = Property(LogicTrue())


@abstract
class RealTypeDef(TypeDef):
    """
    Type definition for real numbers.
    """
    xref_equation = Property(LogicTrue())

    is_static = Property(True)


class DiscreteRange(Struct):
    """
    Represent the range of a discrete type or subtype. The bounds are already
    evaluated, so the type of the fields is BigInt.
    """
    low_bound = UserField(type=T.BigInt)
    high_bound = UserField(type=T.BigInt)


class LogicValResult(Struct):
    """
    Represent the result of a call to logic_val. ``success`` is True iff
    solving the logic equation was successful, and ``value`` holds the value of
    the logic variable.
    """
    success = UserField(type=Bool)
    value = UserField(type=T.AdaNode.entity)


@abstract
class BaseTypeDecl(BasicDecl):
    """
    Base class for type declarations.
    """
    name = Field(type=T.DefiningName)

    env_spec = EnvSpec(add_to_env_kv(Entity.name_symbol, Self))

    defining_names = Property(Entity.name.singleton)

    is_formal_type = Property(Self.parent.is_a(T.GenericFormalTypeDecl))

    @langkit_property(return_type=T.BaseTypeDecl.entity, memoized=True)
    def anonymous_access_type():
        return T.SynthAnonymousTypeDecl.new(
            name=Self.name,
            discriminants=No(T.DiscriminantPart),
            type_def=T.AnonymousTypeAccessDef.new(
                has_not_null=T.NotNullAbsent.new(),
                type_decl=Self
            ),
            aspects=No(T.AspectSpec),
            prims_env=No(T.LexicalEnv)
        ).cast(T.BaseTypeDecl).as_entity

    @langkit_property(return_type=T.BaseTypeDecl.entity)
    def anonymous_access_type_or_null():
        return Entity._.anonymous_access_type

    @langkit_property(
        return_type=T.BaseTypeDecl.entity, public=True, memoized=True
    )
    def private_completion():
        """
        Return the private completion for this type, if there is one.
        """
        return (
            Entity.declarative_scope.cast(T.PublicPart)
            ._.parent.cast(BasePackageDecl)
            ._.private_part._.decls._.find(
                lambda d: d.cast(T.BaseTypeDecl).then(
                    lambda pp:
                    pp.name_symbol == Entity.name_symbol
                )
            ).cast(T.BaseTypeDecl).as_entity
        )

    @langkit_property(return_type=T.BaseTypeDecl.entity)
    def model_of_type():
        """
        Return the type for which this type is a model, if applicable.
        """
        return (
            Entity.get_aspect_expr('Model_Of')
            .cast_or_raise(T.Name).name_designated_type
        )

    @langkit_property(return_type=T.BaseTypeDecl.entity)
    def modeled_type(from_unit=AnalysisUnit):
        """
        Return model type for this type if applicable.
        """
        types_with_models = (Self.top_level_decl(from_unit)
                             .cast_or_raise(T.PackageDecl).public_part
                             .types_with_models)

        return types_with_models.find(lambda t: t.model_of_type == Entity)

    @langkit_property(return_type=Bool)
    def is_view_of_type(comp_view=T.BaseTypeDecl.entity):
        """
        Predicate that will return true if comp_view is a more complete view of
        type typ, or if it is the same view of type typ.
        """
        typ = Var(Entity)
        return Cond(
            comp_view.is_null, False,
            comp_view == typ, True,
            typ.is_view_of_type(comp_view.previous_part(True))
        )

    @langkit_property(dynamic_vars=[origin], return_type=Bool)
    def is_array_or_rec():
        return Entity.is_array | Entity.is_record_type

    @langkit_property(return_type=T.RecordRepClause.entity, public=True,
                      dynamic_vars=[default_imprecise_fallback()])
    def get_record_representation_clause():
        """
        Return the record representation clause associated to this type decl,
        if applicable (i.e. this type decl defines a record type).
        """
        return Entity.declarative_scope._.decls.as_entity.find(
            lambda d: d.cast(T.RecordRepClause).then(
                lambda p: p.name.referenced_decl == Entity
            )
        ).cast(T.RecordRepClause.entity)

    @langkit_property(memoized=True)
    def primitives_env():
        return EmptyEnv

    @langkit_property(public=True)
    def is_record_type():
        """
        Return whether this type is a record type.
        """
        return False

    @langkit_property(public=True, dynamic_vars=[default_origin()])
    def is_array_type():
        """
        Return whether this type is an array type.
        """
        return Entity.is_array

    is_task_type = Property(False, doc="Whether type is a task type")

    is_real_type = Property(False, doc="Whether type is a real type or not.",
                            public=True)
    is_float_type = Property(False, doc="Whether type is a float type or not.",
                             public=True)
    is_fixed_point = Property(
        False, doc="Whether type is a fixed point type or not.", public=True
    )

    is_enum_type = Property(False)
    is_classwide = Property(False)

    is_access_type = Property(
        False, public=True,
        doc="Whether Self is an access type or not",
        dynamic_vars=[(origin, No(T.AdaNode))]
    )

    is_implicit_deref = Property(
        Entity.is_access_type | Not(Entity.get_imp_deref.is_null),
        doc="Whether Self is an implicitly dereferenceable type or not",
        dynamic_vars=[origin]
    )

    has_ud_indexing = Property(
        False, doc="Whether self has user defined indexing or not"
    )

    constant_indexing_fns = Property(
        No(T.BasicDecl.entity.array),
        doc="""
        For a type with user defined indexing, return the set of all
        Constant_Indexing functions.
        """
    )
    variable_indexing_fns = Property(
        No(T.BasicDecl.entity.array),
        doc="""
        For a type with user defined indexing, return the set of all
        Variable_Indexing functions.
        """
    )

    get_imp_deref = Property(
        No(T.Expr.entity),
        doc="If self has an Implicit_Dereference aspect, return its expression"
    )

    access_def = Property(No(T.AccessDef.entity))

    is_char_type = Property(False,
                            doc="Whether type is a character type or not")

    classwide_type = Property(If(
        Entity.is_tagged_type,
        Self.classwide_type_node.as_entity,
        No(T.ClasswideTypeDecl.entity)
    ))

    @langkit_property(dynamic_vars=[origin], return_type=Int)
    def array_ndims():
        return Literal(0)

    @langkit_property(return_type=DiscreteRange)
    def discrete_range():
        """
        Return the discrete range for this type decl, if applicable.
        """
        return No(DiscreteRange)

    @langkit_property(dynamic_vars=[origin], memoized=True)
    def is_iterator_type():
        iifcs = Var(Entity.get_unit_root_decl(
            ['Ada', 'Iterator_Interfaces'], UnitSpecification
        ))
        typ = Var(Entity.cast(T.ClasswideTypeDecl).then(
            lambda cw: cw.typedecl, default_val=Entity)
        )
        return typ.semantic_parent.semantic_parent.node == iifcs

    @langkit_property(dynamic_vars=[default_origin()], public=True)
    def is_discrete_type():
        """
        Whether type is a discrete type or not.
        """
        return Entity.is_int_type | Entity.is_enum_type | Entity.is_char_type

    @langkit_property(dynamic_vars=[default_origin()], public=True)
    def is_int_type():
        """Whether type is an integer type or not."""
        return False

    @langkit_property(dynamic_vars=[origin])
    def is_str_type_or_null():
        return Self.is_null | (
            Entity.is_array & Entity.comp_type._.is_char_type
        )

    is_not_null_char_type = Property(Not(Self.is_null) & Entity.is_char_type)

    @langkit_property(dynamic_vars=[default_origin()], public=True)
    def accessed_type():
        """
        If this type is an access type, or a type with an Implicit_Dereference
        aspect, return the type of a dereference of an instance of this type.
        """
        return No(T.BaseTypeDecl.entity)

    @langkit_property(dynamic_vars=[origin], return_type=T.BaseTypeDecl.entity)
    def final_accessed_type(first_call=(Bool, True)):
        """
        Call accessed_type recursively until we get the most nested accessed
        type. For example, for the following code::

            type A is access Integer;
            type AA is access A;
            type AAA is access AA;

        ``AAA``'s final_accessed_type is Integer.
        """
        return Entity.accessed_type.then(
            lambda at: at.final_accessed_type(False),
            default_val=If(first_call, No(T.BaseTypeDecl.entity), Entity)
        )

    @langkit_property(dynamic_vars=[origin])
    def is_access_to(typ=T.BaseTypeDecl.entity):
        access_type = Var(Entity)
        return access_type.accessed_type.matching_formal_type(typ)

    @langkit_property(dynamic_vars=[origin])
    def is_subp_access_of(entity=T.BasicDecl.entity):
        """
        Returns whether self is an access type whose accessed type matches
        other.
        """
        access_type = Var(Entity)
        return access_type.access_def.cast(AccessToSubpDef).then(
            lambda sa: sa.subp_spec.match_signature(
                entity.subp_spec_or_null.cast(T.SubpSpec), False
            )
        )

    @langkit_property(return_type=Bool)
    def is_generic_formal():
        """
        Return whether this type declaration is a generic formal.
        """
        return Or(
            Self.parent.is_a(GenericFormalTypeDecl),
            Self.parent.cast(BaseTypeDecl)._.is_generic_formal
        )

    is_tagged_type = Property(False, doc="Whether type is tagged or not")
    base_type = Property(
        No(T.BaseTypeDecl.entity), doc="""
        Return the base type entity for this derived type declaration.
        """, public=True
    )

    base_types = Property(
        Entity.base_type.then(lambda bt: bt.singleton)
        .concat(Entity.base_interfaces)
    )

    base_interfaces = Property(No(T.BaseTypeDecl.entity.array))

    record_def = Property(No(T.BaseRecordDef.entity), dynamic_vars=[origin])
    array_def = Property(No(T.ArrayTypeDef.entity), dynamic_vars=[origin])

    @langkit_property(dynamic_vars=[origin])
    def array_def_with_deref():
        """
        Return the array definition corresponding to type `Self` in the context
        of array-indexing, e.g. implicitly dereferencing if `Self` is an
        access.
        """
        return Cond(
            Entity.is_array, Entity.array_def,

            Entity.is_implicit_deref,
            Entity.accessed_type.then(lambda c: c.array_def),

            No(T.ArrayTypeDef.entity)
        )

    is_array_def_with_deref = Property(
        Not(Self.is_null) & Not(Entity.array_def_with_deref.is_null),
        dynamic_vars=[origin]
    )

    @langkit_property(dynamic_vars=[default_origin()],
                      return_type=T.BaseTypeDecl.entity, public=True)
    def comp_type(is_subscript=(Bool, False)):
        """
        Return the component type of `Self`, if applicable. The component type
        is the type you'll get if you call a value whose type is `Self`.  So it
        can either be:

            1. The component type for an array.
            2. The return type for an access to function.
        """
        return Entity.then(
            lambda e: Let(
                lambda ad=If(is_subscript,
                             Entity.array_def_with_deref,
                             Entity.array_def):
                ad.then(lambda ad: ad.comp_type)._or(
                    e.access_def._.match(
                        lambda asd=T.AccessToSubpDef:
                        asd.subp_spec.return_type,
                        lambda tad=T.BaseTypeAccessDef: tad.accessed_type
                    )
                )
            )
        )

    @langkit_property(dynamic_vars=[default_origin()], public=True)
    def index_type(dim=Int):
        """
        Return the index type for dimension ``dim`` for this type, if
        applicable.
        """
        return Entity.array_def_with_deref.then(lambda ad: ad.index_type(dim))

    # A BaseTypeDecl in an expression context corresponds to a type conversion,
    # so its type is itself.
    expr_type = Property(Entity)

    @langkit_property(return_type=Bool,
                      dynamic_vars=[default_origin()], public=True)
    def is_derived_type(other_type=T.BaseTypeDecl.entity):
        """
        Whether Self is derived from other_type.
        """
        entity_can = Var(Entity.canonical_type)
        other_can = Var(other_type.canonical_type)
        return Or(
            entity_can == other_can,
            And(Not(Entity.classwide_type.is_null),
                entity_can.classwide_type == other_can.classwide_type),
            Entity.base_types.any(lambda bt: bt._.is_derived_type(other_type))
        )

    is_iterable_type = Property(
        False,
        doc="""
        Whether Self is a type that is iterable in a for .. of loop
        """,
        dynamic_vars=[origin]
    )

    @langkit_property(dynamic_vars=[origin])
    def iterable_comp_type():
        return No(T.BaseTypeDecl.entity)

    @langkit_property(return_type=Bool, dynamic_vars=[origin])
    def matching_prefix_type(container_type=T.BaseTypeDecl.entity):
        """
        Given a dotted expression A.B, where container_type is the container
        type for B, and Self is a potential type for A, returns whether Self is
        a valid type for A in the dotted expression.
        """
        cont_type = Var(container_type)
        return Or(
            # Derived type case
            Entity.matching_formal_prim_type(cont_type),

            # Access to derived type case
            Entity.final_accessed_type._.matching_formal_prim_type(cont_type),

            # Dot notation: The prefix can be a value type and the formal an
            # access type to this value type.
            cont_type.accessed_type.then(
                lambda at: Entity.matching_formal_prim_type(at)
            ),
        )

    @langkit_property(return_type=Bool, dynamic_vars=[origin])
    def matching_access_type(expected_type=T.BaseTypeDecl.entity,
                             for_assignment=Bool):
        """
        Whether self is a matching access type for expected_type.
        """
        actual_type = Var(Entity)
        return expected_type.match(
            lambda atd=T.AnonymousTypeDecl.entity:
            atd.access_def_matches(actual_type, for_assignment),
            lambda _: actual_type.match(
                lambda atd2=T.AnonymousTypeDecl.entity:
                atd2.access_def_matches(expected_type, for_assignment),
                lambda _: False
            )
        )

    @langkit_property(return_type=Bool, dynamic_vars=[origin])
    def matching_formal_prim_type(formal_type=T.BaseTypeDecl.entity):
        return Entity.matching_formal_type_impl(formal_type, True)

    @langkit_property(return_type=Bool, dynamic_vars=[origin])
    def matching_formal_type_inverted(formal_type=T.BaseTypeDecl.entity):
        return formal_type.matching_formal_type_impl(Entity)

    @langkit_property(return_type=Bool, dynamic_vars=[origin])
    def matching_formal_type(formal_type=T.BaseTypeDecl.entity):
        return Entity.matching_formal_type_impl(formal_type)

    @langkit_property(return_type=Bool, dynamic_vars=[origin])
    def matching_formal_type_impl(formal_type=T.BaseTypeDecl.entity,
                                  accept_derived=(Bool, False)):
        actual_type = Var(Entity)
        return Or(
            And(formal_type.is_classwide | accept_derived,
                actual_type.is_derived_type(formal_type)),

            And(actual_type.is_classwide,
                actual_type.is_derived_type(formal_type)),

            # Matching of access types parameters
            actual_type.accessed_type.then(
                lambda actual_access:
                formal_type.accessed_type.then(
                    lambda formal_access:
                    And(formal_access.is_classwide | accept_derived,
                        actual_access.is_derived_type(formal_access))
                )
            ),

            And(Not(actual_type.get_imp_deref.is_null),
                actual_type
                .accessed_type.matching_formal_type(formal_type)),

            actual_type.matching_type(formal_type)
        )

    @langkit_property(return_type=Bool, dynamic_vars=[origin])
    def matching_assign_type(expected_type=T.BaseTypeDecl.entity):
        actual_type = Var(Entity)
        return Or(
            Entity.matching_type(expected_type),

            And(
                expected_type.is_classwide,
                actual_type.matching_formal_prim_type(expected_type)
            ),

            And(
                Not(actual_type.get_imp_deref.is_null),
                actual_type
                .accessed_type.matching_assign_type(expected_type)
            ),

            And(
                Not(expected_type.get_imp_deref.is_null),
                expected_type
                .accessed_type.matching_assign_type(actual_type)
            ),

            Entity.matching_access_type(expected_type, True)
        )

    @langkit_property(return_type=Bool,
                      dynamic_vars=[default_origin()], public=True)
    def matching_type(expected_type=T.BaseTypeDecl.entity):
        """
        Return whether ``self`` matches ``expected_type``.
        """
        actual_type = Var(Entity)
        return Or(
            And(actual_type == Self.universal_int_type,
                expected_type.is_int_type),

            And(expected_type == Self.universal_int_type,
                actual_type.is_int_type),

            And(actual_type == Self.universal_real_type,
                expected_type.is_real_type),

            And(expected_type == Self.universal_real_type,
                actual_type.is_real_type),

            And(Not(expected_type.is_null),
                Not(actual_type.is_null),
                Or(actual_type.canonical_type == expected_type.canonical_type,
                   actual_type.matching_access_type(expected_type, False)))
        )

    @langkit_property(return_type=Bool, dynamic_vars=[origin])
    def matching_allocator_type(allocated_type=T.BaseTypeDecl.entity):
        return And(
            Entity.is_access_type,
            allocated_type.matching_formal_type(Entity.accessed_type)
        )

    @langkit_property(return_type=T.BaseTypeDecl.entity,
                      dynamic_vars=[(origin, No(T.AdaNode))], public=True)
    def canonical_type():
        """
        Return the canonical type declaration for this type declaration. For
        subtypes, it will return the base type declaration.
        """
        return Entity.canonical_part.cast(T.BaseTypeDecl)

    @langkit_property(return_type=T.BaseTypeDecl.entity,
                      dynamic_vars=[(origin, No(T.AdaNode))])
    def canonical_type_or_null():
        return Entity._.canonical_type

    @langkit_property(memoized=True, memoize_in_populate=True,
                      ignore_warn_on_node=True)
    def classwide_type_node():
        return T.ClasswideTypeDecl.new(name=Self.name)

    @langkit_property(
        memoized=True, memoize_in_populate=True, ignore_warn_on_node=True
    )
    def scalar_base_subtype_node():
        """
        Helper for scalar_base_subtype. Return the interned node for the
        subtype entity.
        """
        return DiscreteBaseSubtypeDecl.new(name=Self.name)

    @langkit_property()
    def scalar_base_subtype():
        """
        Return the base subtype for this type. Note that this is only legal for
        scalar types.
        """
        return Self.scalar_base_subtype_node().as_entity

    @langkit_property(public=True,
                      return_type=T.BaseTypeDecl.entity,
                      memoized=True)
    def previous_part(go_to_incomplete=(Bool, True)):
        """
        Returns the previous part for this type decl.
        """
        return Self.name.then(
            lambda type_name:

            env_get(Entity.children_env, type_name.name_symbol,
                    from_node=Self, categories=noprims)
            .then(lambda previous_parts: previous_parts.find(lambda pp: Or(
                And(Entity.is_in_private_part,
                    pp.cast(T.BaseTypeDecl)._.is_private),
                And(go_to_incomplete,
                    pp.is_a(T.IncompleteTypeDecl)),
            ))).cast(T.BaseTypeDecl)
        )

    @langkit_property(public=True, return_type=T.BaseTypeDecl.entity,
                      memoized=True)
    def next_part():
        """
        Returns the next part for this type decl.
        """

        return Entity.match(
            lambda itd=T.IncompleteTypeDecl:
            # The next part of a (non-private) incomplete type declaration must
            # either be in the same declarative scope...
            itd.node_env
            .get(itd.name.name_symbol, LK.flat, categories=noprims)
            .find(lambda t: t.is_a(BaseTypeDecl) & (t != Entity))

            # Or in the particular case of taft-amendment types where the
            # incomplete decl is in the private part of the package spec,
            # the next part can be found in the package's body (RM 3.10.1).
            ._or(Entity.is_in_private_part.then(
                lambda _:
                Entity.declarative_scope.parent
                .cast_or_raise(T.BasePackageDecl).as_entity.body_part.then(
                    lambda p: p.children_env
                    .get(itd.name.name_symbol, LK.flat, categories=noprims)
                    .find(lambda t: t.is_a(BaseTypeDecl))
                )
            )).cast(BaseTypeDecl),

            lambda _: If(
                Entity.is_private
                & Not(Entity.is_generic_formal),
                bind_origin(Self, Entity.canonical_type).then(
                    lambda ct:
                    Entity.declarative_scope.parent
                    .cast_or_raise(T.BasePackageDecl).then(
                        lambda p: p.private_part.children_env
                        .get(ct.name.name_symbol, LK.flat, categories=noprims)
                        .find(lambda t: t.is_a(BaseTypeDecl) & (t != ct))
                        .cast(BaseTypeDecl),
                    ),
                ),

                No(T.BaseTypeDecl.entity)
            )

        )

    @langkit_property(return_type=T.BaseTypeDecl.entity,
                      public=True)
    def full_view():
        """
        Return the full completion of this type.
        """
        return Entity.next_part.then(
            lambda np: np.full_view,
            default_val=Entity
        )

    @langkit_property(return_type=Bool,
                      dynamic_vars=[default_origin()], public=True)
    def is_definite_subtype():
        """
        Returns whether this is a definite subtype.

        For convenience, this will return ``False`` for incomplete types, even
        though the correct answer is more akin to "non applicable".
        """
        return Entity.match(
            lambda _=T.IncompleteTypeDecl: False,
            lambda td=T.TypeDecl: td.discriminants.is_null & td.type_def.match(
                lambda dtd=T.DerivedTypeDef:
                Not(dtd.subtype_indication.constraint.is_null)
                | dtd.base_type.is_definite_subtype,

                lambda atd=T.ArrayTypeDef:
                atd.indices.is_a(T.ConstrainedArrayIndices),
                lambda _: True,
            ),
            lambda st=T.SubtypeDecl:
            Not(st.subtype.constraint.is_null)
            | st.from_type.is_definite_subtype,
            lambda _=T.ClasswideTypeDecl: False,
            lambda ttd=T.TaskTypeDecl: ttd.discriminants.is_null,
            lambda ptd=T.ProtectedTypeDecl: ptd.discriminants.is_null,
            lambda _: True
        )

    is_private = Property(
        False,
        doc="""
        Whether node is a private view of corresponding type.
        """,
        public=True,
    )

    discriminants_list = AbstractProperty(
        type=BaseFormalParamDecl.entity.array
    )

    root_type = Property(Entity)

    next_part_for_decl = Property(Entity.match(
        lambda ttd=T.TaskTypeDecl: ttd.basic_decl_next_part_for_decl,
        lambda _: Entity.next_part.cast(T.BasicDecl.entity)
    ))


@synthetic
class ClasswideTypeDecl(BaseTypeDecl):
    """
    Synthetic node (not parsed, generated from a property call). Refers to the
    classwide type for a given tagged type. The aim is that those be mostly
    equivalent to their non-classwide type, except for some resolution rules.
    """
    # We don't want to add the classwide type to the environment
    env_spec = EnvSpec()

    aspects = NullField()

    typedecl = Property(Self.parent.cast(BaseTypeDecl).as_entity)

    is_classwide = Property(True)

    is_tagged_type = Property(True)
    base_type = Property(Entity.typedecl.base_type)
    base_interfaces = Property(Entity.typedecl.base_interfaces)
    record_def = Property(Entity.typedecl.record_def)
    classwide_type = Property(Entity)
    is_iterable_type = Property(Entity.typedecl.is_iterable_type)
    iterable_comp_type = Property(Entity.typedecl.iterable_comp_type)
    defining_env = Property(Entity.typedecl.defining_env)
    is_private = Property(Entity.typedecl.is_private)
    is_in_private_part = Property(Entity.typedecl.is_in_private_part)

    @langkit_property()
    def get_aspect(name=Symbol):
        return Entity.typedecl.get_aspect(name)

    discriminants_list = Property(Entity.typedecl.discriminants_list)

    @langkit_property(public=True, return_type=T.BaseTypeDecl.entity,
                      memoized=True)
    def previous_part(go_to_incomplete=(Bool, True)):
        return Entity.typedecl.previous_part(go_to_incomplete).then(
            lambda pp: pp.classwide_type
        )

    canonical_type = Property(Entity.typedecl.canonical_type)


class TypeDecl(BaseTypeDecl):
    """
    Type declarations that embed a type definition node.
    """
    discriminants = Field(type=T.DiscriminantPart)
    type_def = Field(type=T.TypeDef)
    aspects = Field(type=T.AspectSpec)
    prims_env = UserField(type=T.LexicalEnv, public=False)

    is_iterable_type = Property(
        # TODO: Need to implement on:
        #
        #   * Spark iterable types (Iterable aspect).
        Or(
            Entity.is_array,
            Not(Entity.get_aspect_expr('Iterator_Element').is_null),
            Not(Entity.get_aspect_expr('Iterable').is_null),
            Entity.type_def.match(
                lambda dtd=T.DerivedTypeDef:
                dtd.base_type.then(lambda bt: bt.is_iterable_type),
                lambda _: False
            ),
            Entity.previous_part(False).then(lambda pp: pp.is_iterable_type)
        ),
        doc="""
        Whether Self is a type that is iterable in a for .. of loop
        """,
        dynamic_vars=[origin]
    )

    @langkit_property()
    def iterable_comp_type():
        ie = Var(Entity.get_aspect_expr('Iterator_Element'))
        it = Var(Entity.get_aspect_expr('Iterable'))

        return imprecise_fallback.bind(False, Cond(
            Entity.is_array, Entity.comp_type,

            Not(ie.is_null), ie.cast(T.Name).then(
                lambda name: env.bind(name.node_env, name.designated_type_impl)
            ),

            Not(it.is_null),
            it.cast(T.Aggregate).assocs.unpacked_params.find(
                lambda sa: sa.name.name_is('Element')
            ).assoc.expr.cast_or_raise(T.Name)
            .referenced_decl.expr_type,

            Entity.type_def.match(
                lambda dtd=T.DerivedTypeDef:
                dtd.base_type.then(lambda bt: bt.iterable_comp_type),
                lambda _: No(T.BaseTypeDecl.entity)
            ),
        )._or(Entity.previous_part(False)
              .then(lambda pp: pp.iterable_comp_type)))

    @langkit_property()
    def discrete_range():
        return Entity.type_def.discrete_range

    @langkit_property()
    def discriminants_list():
        base_type = Var(Entity.base_type)
        self_discs = Var(Entity.discriminants.then(
            lambda d: d.abstract_formal_params)
        )

        return bind_origin(Self, Cond(
            Entity.is_access_type,
            Entity.accessed_type.discriminants_list,

            self_discs.length > 0, self_discs,
            Not(base_type.is_null), Entity.base_type.discriminants_list,
            No(T.BaseFormalParamDecl.entity.array)
        ))

    @langkit_property(external=True, uses_entity_info=False, uses_envs=True,
                      return_type=LexicalEnv)
    def primitives():
        pass

    array_ndims = Property(Entity.type_def.array_ndims)

    is_record_type = Property(Entity.type_def.is_record_type)
    is_real_type = Property(Entity.type_def.is_real_type)
    is_float_type = Property(Entity.type_def.is_float_type)
    is_fixed_point = Property(Entity.type_def.is_fixed_point)
    is_int_type = Property(Entity.type_def.is_int_type)
    is_access_type = Property(Self.as_bare_entity.type_def.is_access_type)
    is_static_decl = Property(Self.as_bare_entity.type_def.is_static)

    @langkit_property()
    def accessed_type():
        imp_deref = Var(Entity.get_imp_deref)

        return If(
            imp_deref.is_null,
            Entity.type_def.accessed_type,

            # Here, we need to call defining_env on TypeDef, in order to not
            # recurse for ever (accessed_type is called by defining_env).
            Entity.type_def.defining_env.get_first(
                imp_deref.cast(T.Name).name_symbol, categories=noprims
            )

            # We cast to BaseFormalParamDecl. Following Ada's legality rule,
            # you need to implicit deref on a discriminant, but I see no reason
            # to enforce that here.
            .cast_or_raise(T.BaseFormalParamDecl).formal_type.accessed_type
        )

    access_def = Property(Entity.type_def.match(
        lambda ad=T.AccessDef: ad,
        lambda dtd=T.DerivedTypeDef: dtd.base_type.access_def,
        lambda _: No(T.AccessDef.entity)
    ))

    is_tagged_type = Property(Entity.type_def.is_tagged_type)
    base_type = Property(Entity.type_def.base_type)
    base_interfaces = Property(Entity.type_def.base_interfaces)
    is_char_type = Property(Entity.type_def.is_char_type)
    is_enum_type = Property(Entity.type_def.is_enum_type)
    is_private = Property(
        Self.type_def.is_a(T.PrivateTypeDef)
        | Self.type_def.cast(T.DerivedTypeDef).then(
            lambda dtd: dtd.has_with_private.as_bool
        )
    )

    array_def = Property(Entity.type_def.match(
        lambda atd=T.ArrayTypeDef: atd,
        lambda dtd=T.DerivedTypeDef: dtd.base_type.array_def,
        lambda _: No(T.ArrayTypeDef.entity)
    ))

    root_type = Property(Entity.type_def.match(
        lambda dtd=T.DerivedTypeDef: dtd.base_type.root_type,
        lambda _: Entity
    ))

    @langkit_property()
    def defining_env():
        imp_deref = Var(Entity.get_imp_deref)

        # Evaluating in type env, because the defining environment of a type
        # is always its own.
        self_env = Entity.type_def.defining_env

        return Cond(
            Not(imp_deref.is_null),
            Array([self_env, Entity.accessed_type.defining_env]).env_group(),

            Entity.has_ud_indexing,
            Entity.constant_indexing_fns
            .concat(Entity.variable_indexing_fns)
            .map(lambda fn: fn.defining_env)
            .concat([self_env]).env_group(),

            self_env,
        )

    env_spec = EnvSpec(
        add_to_env_kv(Entity.name_symbol, Self),
        add_env(),
        handle_children(),
        reference(
            Self.cast(AdaNode).singleton,
            through=T.TypeDecl.parent_primitives_env,
            kind=RefKind.transitive,
            dest_env=Self.node_env,
            cond=Self.type_def.is_a(T.DerivedTypeDef),
            category="inherited_primitives"
        ),
    )

    record_def = Property(
        Entity.type_def.match(
            lambda r=T.RecordTypeDef: r.record_def,
            # If the derived type is tagged, then return its own record def. If
            # it isn't tagged, return the base type's record def.
            lambda d=T.DerivedTypeDef: If(
                Entity.is_tagged_type,
                d.record_extension,
                d.base_type._.record_def
            ),
            lambda _: No(T.BaseRecordDef.entity)
        )
    )

    xref_entry_point = Property(True)

    @langkit_property(return_type=Equation)
    def xref_equation():
        # TODO: Handle discriminants
        return Entity.type_def.sub_equation

    is_discrete_type = Property(Entity.type_def.is_discrete_type)

    @langkit_property(return_type=LexicalEnv)
    def own_primitives_env():
        """
        Return the environment containing the primitives for Self.
        """
        own_rebindings = Var(Entity.info.rebindings)
        return Entity.primitives.rebind_env(own_rebindings)

    @langkit_property(return_type=LexicalEnv.array)
    def own_primitives_envs():
        """
        Return the environments containing the primitives for Self and its
        previous parts, if there are some.
        """
        # If self has a previous part, it might have primitives too
        return Entity.previous_part(False).cast(T.TypeDecl).then(
            lambda pp: Array([
                Entity.own_primitives_env, pp.own_primitives_env
            ]),
            default_val=Entity.own_primitives_env.singleton
        )

    @langkit_property(return_type=LexicalEnv.array)
    def primitives_envs(include_self=(Bool, False)):
        """
        Return the environments containing the primitives for Self and all its
        base types.
        """
        return Entity.base_types.mapcat(lambda t: t.match(
            lambda td=T.TypeDecl: td,
            lambda std=T.SubtypeDecl: bind_origin(
                std.node, std.from_type.cast(T.TypeDecl)
            ),
            lambda _: No(T.TypeDecl.entity),
        ).then(lambda bt: bt.own_primitives_envs.concat(bt.primitives_envs))
        ).concat(
            If(include_self, Entity.own_primitives_envs, No(LexicalEnv.array))
        )

    @langkit_property(memoized=True, return_type=T.PrimTypeAccessor,
                      ignore_warn_on_node=True)
    def primitive_type_accessor():
        """
        Return a synthetic node that wraps around this type as an entity. This
        works around the fact that we cannot store an entity in the entity
        info, allowing us to access the full primitive_real_type.
        """
        return T.PrimTypeAccessor.new(prim_type=Entity)

    @langkit_property(memoized=True)
    def compute_primitives_env(include_self=(Bool, True)):
        """
        Return a environment containing all primitives accessible to Self,
        with the adjusted `primitive_real_type` metadata field.
        """
        return Entity.primitives_envs(include_self=include_self).env_group(
            with_md=new_metadata(
                primitive_real_type=Entity.primitive_type_accessor
            )
        )

    @langkit_property()
    def parent_primitives_env():
        return Self.type_def.match(
            lambda _=T.DerivedTypeDef:
            Entity.compute_primitives_env(include_self=False),

            lambda _: Self.empty_env
        )

    @langkit_property()
    def primitives_env():
        return Entity.compute_primitives_env(include_self=True)

    get_imp_deref = Property(Entity.get_aspect_expr('Implicit_Dereference'))

    has_ud_indexing = Property(
        Not(Entity.get_aspect_expr('Constant_Indexing').is_null)
        | Not(Entity.get_aspect_expr('Variable_Indexing').is_null)
    )

    @langkit_property()
    def constant_indexing_fns():
        return (
            Entity.get_aspect_expr('Constant_Indexing')._.cast_or_raise(T.Name)
            .all_env_elements(seq=False).filtermap(
                lambda e: e.cast(T.BasicDecl),
                lambda env_el:
                env_el.cast_or_raise(T.BasicDecl).subp_spec_or_null.then(
                    lambda ss:
                    bind_origin(
                        Self,
                        ss.unpacked_formal_params.at(0)
                        ._.spec.formal_type.matching_formal_type(Entity)
                    )
                )
            )
        )

    @langkit_property()
    def variable_indexing_fns():
        return bind_origin(
            Self,
            Entity.get_aspect_expr('Variable_Indexing').then(
                lambda a: a.cast_or_raise(T.Name).all_env_elements(seq=False)
                .filtermap(
                    lambda e: e.cast(T.BasicDecl),
                    lambda env_el:
                    env_el.cast_or_raise(T.BasicDecl).subp_spec_or_null.then(
                        lambda ss:
                        ss.unpacked_formal_params.at(0)
                        ._.spec.formal_type.matching_formal_type(Entity)
                        & ss.return_type.is_implicit_deref
                    )
                )
            )
        )


@synthetic
class PrimTypeAccessor(AdaNode):
    """
    Synthetic node wrapping around a primitive type entity. Used in metadata.
    """
    prim_type = UserField(T.BaseTypeDecl.entity, public=False)

    get_prim_type = Property(Self.prim_type)


class AnonymousTypeDecl(TypeDecl):
    """
    Anonymous type declaration (for anonymous array or access types).
    """

    @langkit_property(return_type=Bool, dynamic_vars=[origin])
    def access_def_matches(other=BaseTypeDecl.entity, for_assignment=Bool):
        """
        Returns whether:
          1. Self and other are both access types.
          2. Their access def matches structurally. If for_assignment is True,
             matching_assign_type is used instead of matching_type to compare
             the two access defs.
        """

        # If the anonymous type is an access type definition, then verify if
        #  the accessed type corresponds to other's accessed type.
        return Entity.type_def.cast(AccessDef)._.match(
            lambda asp=AccessToSubpDef:
            other.access_def.cast(AccessToSubpDef).then(
                lambda sa: sa.subp_spec.match_signature(
                    asp.subp_spec, False
                )
            ),
            lambda ad:
            ad.accessed_type.then(
                lambda ast: other.accessed_type.then(
                    lambda oat: If(
                        for_assignment,
                        oat.matching_assign_type(ast),
                        oat.matching_type(ast)
                    )
                )
            )
        )

    xref_entry_point = Property(False)

    # We don't want to add anonymous type declarations to the lexical
    # environments, so we reset the env spec.
    env_spec = EnvSpec()


@synthetic
class SynthAnonymousTypeDecl(AnonymousTypeDecl):
    """
    Synthetic anonymous type decl. Used to generate anonymous access types.
    """
    pass


class EnumTypeDef(TypeDef):
    """
    Type definition for enumerations.
    """
    enum_literals = Field(type=T.EnumLiteralDecl.list)

    is_char_type = Property(Self.enum_literals.any(
        lambda lit: lit.name.name.is_a(T.CharLiteral)
    ))

    is_enum_type = Property(True)

    xref_equation = Property(LogicTrue())

    is_static = Property(True)


class FloatingPointDef(RealTypeDef):
    """
    Type definition for floating-point numbers.
    """
    num_digits = Field(type=T.Expr)
    range = Field(type=T.RangeSpec)
    is_float_type = Property(True)


class OrdinaryFixedPointDef(RealTypeDef):
    """
    Type definition for ordinary fixed-point numbers.
    """
    delta = Field(type=T.Expr)
    range = Field(type=T.RangeSpec)

    is_fixed_point = Property(True)


class DecimalFixedPointDef(RealTypeDef):
    """
    Type definition for decimal fixed-point numbers.
    """
    delta = Field(type=T.Expr)
    digits = Field(type=T.Expr)
    range = Field(type=T.RangeSpec)

    is_fixed_point = Property(True)


@abstract
class BaseAssoc(AdaNode):
    """
    Abstract class for a key/value association, where the value is an
    expression.
    """
    assoc_expr = AbstractProperty(
        type=T.Expr.entity, public=True,
        doc="Returns the expression side of this assoc node."
    )


@abstract
class Constraint(AdaNode):
    """
    Base class for type constraints.
    """
    subtype = Property(bind_origin(
        Self,
        Self.parent.cast_or_raise(T.SubtypeIndication)
        .as_entity.designated_type
    ))

    @langkit_property(dynamic_vars=[default_imprecise_fallback()])
    def is_static():
        return Entity.match(
            lambda rc=RangeConstraint: rc.range.range.is_static_expr,
            lambda ic=IndexConstraint:
            ic.constraints.all(lambda c: c.match(
                lambda st=SubtypeIndication: st.is_static_subtype,
                lambda e=Expr: e.is_static_expr,
                lambda _: False
            )),
            lambda dc=DiscriminantConstraint: dc.constraints.all(
                lambda c: c.expr.is_static_expr
            ),
            # TODO: Handle constraints for floating point types
            lambda _: False
        )


class RangeConstraint(Constraint):
    """
    Range-based type constraint.
    """
    range = Field(type=T.RangeSpec)

    @langkit_property()
    def xref_equation():
        return And(
            TypeBind(Entity.range.range.type_var, Entity.subtype),
            Entity.range.sub_equation
        )


class DigitsConstraint(Constraint):
    """
    Digits and range type constraint.
    """
    digits = Field(type=T.Expr)
    range = Field(type=T.RangeSpec)

    xref_equation = Property(
        Entity.digits.sub_equation & Entity.range.sub_equation
    )


class DeltaConstraint(Constraint):
    """
    Delta and range type constraint.
    """
    digits = Field(type=T.Expr)
    range = Field(type=T.RangeSpec)

    xref_equation = Property(
        Entity.digits.sub_equation & Entity.range.sub_equation
    )


class IndexConstraint(Constraint):
    """
    List of type constraints.
    """
    constraints = Field(type=T.ConstraintList)

    xref_equation = Property(
        Entity.constraints.logic_all(lambda c: c.xref_equation)
    )


class DiscriminantConstraint(Constraint):
    """
    List of constraints that relate to type discriminants.
    """
    constraints = Field(type=T.AssocList)

    @langkit_property()
    def xref_equation():
        typ = Var(Entity.subtype)

        return If(
            # Due to ambiguities in the grammar, this can actually be parsed as
            # a DiscriminantConstraint but be an index constraint.
            typ.is_array,

            # Index constraints imply no overloading
            Entity.constraints.logic_all(
                lambda c: c.expr.sub_equation
            ),

            # Regular discriminant constraint case
            Self.match_formals(
                typ.discriminants_list, Entity.constraints, False
            ).logic_all(
                lambda pm: pm.actual.assoc.expr.xref_equation
                & Bind(
                    pm.actual.assoc.expr.type_var, pm.formal.spec.formal_type,
                    eq_prop=BaseTypeDecl.matching_formal_type
                )
            )
        )


@abstract
@has_abstract_list
class BasicAssoc(AdaNode):
    """
    Association of one or several names to an expression.
    """
    expr = AbstractProperty(type=T.Expr.entity)
    names = AbstractProperty(type=T.AdaNode.array)

    @langkit_property(public=True,
                      return_type=T.DefiningName.entity.array,
                      dynamic_vars=[default_imprecise_fallback()])
    def get_params():
        """
        Return the list of parameters that this association refers to.
        """
        return (
            Entity.parent.cast_or_raise(T.AssocList).zip_with_params.filtermap(
                lambda m: m.param,
                lambda m: m.actual == Entity.expr
            )
        )


class DiscriminantAssoc(BasicAssoc):
    """
    Association of discriminant names to an expression.
    """
    ids = Field(type=T.DiscriminantChoiceList)
    discr_expr = Field(type=T.Expr)

    expr = Property(Entity.discr_expr)
    names = Property(Self.ids.map(lambda i: i.cast(T.AdaNode)))


class DerivedTypeDef(TypeDef):
    """
    Type definition for a derived type.
    """
    has_abstract = Field(type=Abstract)
    has_limited = Field(type=Limited)
    has_synchronized = Field(type=Synchronized)
    subtype_indication = Field(type=T.SubtypeIndication)
    interfaces = Field(type=T.ParentList)
    record_extension = Field(type=T.BaseRecordDef)
    has_with_private = Field(type=WithPrivate)

    array_ndims = Property(Entity.base_type.array_ndims)

    # TODO: this origin bind is erroneous
    base_type = Property(
        bind_origin(Self, Entity.subtype_indication.designated_type)
    )

    base_interfaces = Property(
        Entity.interfaces.map(lambda i: i.name_designated_type)
    )

    is_int_type = Property(Entity.base_type.is_int_type)
    is_access_type = Property(Self.as_bare_entity.base_type.is_access_type)
    is_char_type = Property(Entity.base_type.is_char_type)
    is_float_type = Property(Entity.base_type.is_float_type)
    is_fixed_point = Property(Entity.base_type.is_fixed_point)
    accessed_type = Property(Entity.base_type.accessed_type)
    is_tagged_type = Property(
        Not(Entity.record_extension.is_null) | Entity.has_with_private.as_bool
    )

    is_enum_type = Property(Entity.base_type.is_enum_type)
    is_record_type = Property(
        Entity.is_tagged_type | Entity.base_type.is_record_type
    )
    is_static = Property(Entity.subtype_indication.is_static_subtype)

    @langkit_property(return_type=Equation)
    def xref_equation():
        # We want to make discriminants accessible, so need to evaluate this in
        # Self's children_env.
        return env.bind(Self.children_env, (
            Entity.subtype_indication.xref_equation
            & Entity.interfaces.logic_all(lambda ifc: ifc.xref_equation)
        ))

    @langkit_property()
    def discrete_range():
        return Entity.subtype_indication.discrete_range


class PrivateTypeDef(TypeDef):
    """
    Type definition for a private type.
    """
    has_abstract = Field(type=Abstract)
    has_tagged = Field(type=Tagged)
    has_limited = Field(type=Limited)

    is_tagged_type = Property(Self.has_tagged.as_bool)

    xref_equation = Property(LogicTrue())


class SignedIntTypeDef(TypeDef):
    """
    Type definition for a signed integer type.
    """
    range = Field(type=T.RangeSpec)
    is_int_type = Property(True)

    xref_equation = Property(
        # We try to bind the range expression's type to the type we're
        # defining. If not possible (for example because it's already of
        # another type), fallback.
        Or(
            TypeBind(Entity.range.range.type_var, Entity.containing_type),
            LogicTrue()
        )
        & Entity.range.xref_equation
    )

    @langkit_property()
    def discrete_range():
        return Entity.range.range.discrete_range

    is_static = Property(Entity.range.range.is_static_expr)


class ModIntTypeDef(TypeDef):
    """
    Type definition for a modular integer type.
    """
    expr = Field(type=T.Expr)
    is_int_type = Property(True)

    xref_equation = Property(Entity.expr.sub_equation)

    is_static = Property(Entity.expr.is_static_expr)

    @langkit_property()
    def discrete_range():
        return DiscreteRange.new(low_bound=BigIntLiteral(0),
                                 high_bound=Self.expr.eval_as_int)


@abstract
class ArrayIndices(AdaNode):
    """
    Specification for array indexes.
    """
    ndims = AbstractProperty(
        type=Int,
        doc="""Number of dimensions described in this node."""
    )

    @langkit_property(return_type=Equation, dynamic_vars=[origin],
                      kind=AbstractKind.abstract)
    def constrain_index_expr(index_expr=T.Expr.entity, dim=Int):
        """
        Add a constraint on an expression passed as the index of an array
        access expression.

        For example::

            type A is array (Integer range 1 .. 10) of Integer;

            A_Inst : A;

            A_Inst (2);
            --      ^ Will add constraint on lit that it needs to be of type
            --      Integer.
        """
        pass

    @langkit_property(dynamic_vars=[origin], kind=AbstractKind.abstract,
                      return_type=T.BaseTypeDecl.entity)
    def index_type(dim=Int):
        pass


class UnconstrainedArrayIndices(ArrayIndices):
    """
    Unconstrained specification for array indexes.
    """
    types = Field(type=T.UnconstrainedArrayIndex.list)
    ndims = Property(Self.types.length)

    @langkit_property(return_type=Equation)
    def constrain_index_expr(index_expr=T.Expr.entity, dim=Int):
        return TypeBind(index_expr.type_var, Entity.index_type(dim))

    @langkit_property()
    def index_type(dim=Int):
        return Entity.types.at(dim)._.designated_type

    @langkit_property()
    def xref_equation():
        return Entity.types.logic_all(
            lambda typ: typ.subtype_indication.sub_equation
        )


class ConstrainedArrayIndices(ArrayIndices):
    """
    Constrained specification for array indexes.
    """
    list = Field(type=T.ConstraintList)

    ndims = Property(Self.list.length)

    @langkit_property(return_type=Equation)
    def constrain_index_expr(index_expr=T.Expr.entity, dim=Int):
        return TypeBind(index_expr.type_var, Entity.index_type(dim))

    @langkit_property()
    def xref_equation():
        return Entity.list.logic_all(
            lambda index:
            index.sub_equation
            & index.cast(T.Expr).then(
                lambda expr:
                TypeBind(expr.type_var, Self.int_type)
                | Predicate(BaseTypeDecl.is_discrete_type, expr.type_var),
                default_val=LogicTrue()
            )
        )

    @langkit_property(dynamic_vars=[origin])
    def index_type(dim=Int):
        # We might need to solve self's equation to get the index type
        ignore(Var(Self.parents.find(
            lambda p: p.xref_entry_point).as_entity.resolve_names
        ))

        return Entity.list.at(dim)._.match(
            lambda st=T.SubtypeIndication: st.designated_type,
            lambda e=T.Expr: e.type_val.cast(T.BaseTypeDecl.entity),
            lambda _: No(T.BaseTypeDecl.entity)
        )


class ComponentDef(AdaNode):
    """
    Definition for a component.
    """
    has_aliased = Field(type=Aliased)
    has_constant = Field(type=Constant)
    type_expr = Field(type=T.TypeExpr)

    @langkit_property()
    def xref_equation():
        return Entity.type_expr.sub_equation


class ArrayTypeDef(TypeDef):
    """
    Type definition for an array.
    """
    indices = Field(type=T.ArrayIndices)
    component_type = Field(type=T.ComponentDef)

    @langkit_property(dynamic_vars=[origin])
    def comp_type():
        """Returns the type stored as a component in the array."""
        return (Entity.component_type.type_expr.designated_type)

    @langkit_property(dynamic_vars=[origin])
    def index_type(dim=Int):
        return Entity.indices.index_type(dim)

    array_ndims = Property(Self.indices.ndims)

    @langkit_property()
    def xref_equation():
        return And(
            Entity.indices.sub_equation,
            Entity.component_type.sub_equation
        )

    xref_entry_point = Property(True)


class InterfaceKind(AdaNode):
    """
    Kind of interface type.
    """
    enum_node = True
    alternatives = ["limited", "task", "protected", "synchronized"]


class InterfaceTypeDef(TypeDef):
    """
    Type definition for an interface.
    """
    interface_kind = Field(type=InterfaceKind)
    interfaces = Field(type=T.ParentList)

    is_tagged_type = Property(True)

    base_interfaces = Property(
        Entity.interfaces.map(lambda i: i.name_designated_type)
    )

    @langkit_property(return_type=Equation)
    def xref_equation():
        return Entity.interfaces.logic_all(lambda ifc: ifc.xref_equation)


@abstract
class BaseSubtypeDecl(BaseTypeDecl):
    """
    Base class for subtype declarations.
    """
    @langkit_property(return_type=T.BaseTypeDecl.entity)
    def from_type_bound():
        # TODO: This is a hack, to avoid making all of the predicates on types
        # take an origin. But ultimately, for semantic correctness, it will be
        # necessary to remove this, and migrate every property using it to
        # having a dynamic origin parameter.
        return bind_origin(Self, Entity.from_type)

    @langkit_property(kind=AbstractKind.abstract,
                      return_type=T.BaseTypeDecl.entity, dynamic_vars=[origin])
    def from_type():
        pass

    primitives_env = Property(Entity.from_type_bound.primitives_env)

    array_ndims = Property(Entity.from_type.array_ndims)
    defining_env = Property(Entity.from_type.defining_env)

    canonical_type = Property(Entity.from_type.canonical_type)
    record_def = Property(Entity.from_type.record_def)
    accessed_type = Property(Entity.from_type.accessed_type)
    is_int_type = Property(Entity.from_type.is_int_type)
    is_discrete_type = Property(Entity.from_type.is_discrete_type)
    is_real_type = Property(Entity.from_type_bound.is_real_type)
    is_float_type = Property(Entity.from_type_bound.is_float_type)
    is_fixed_point = Property(Entity.from_type_bound.is_fixed_point)
    is_enum_type = Property(Entity.from_type_bound.is_enum_type)
    is_access_type = Property(Entity.from_type.is_access_type)
    access_def = Property(Entity.from_type_bound.access_def)
    is_char_type = Property(Entity.from_type_bound.is_char_type)
    is_tagged_type = Property(Entity.from_type_bound.is_tagged_type)
    base_type = Property(Entity.from_type_bound.base_type)
    array_def = Property(Entity.from_type.array_def)
    is_classwide = Property(Entity.from_type_bound.is_classwide)
    discriminants_list = Property(Entity.from_type_bound.discriminants_list)
    is_iterable_type = Property(Entity.from_type.is_iterable_type)
    iterable_comp_type = Property(Entity.from_type.iterable_comp_type)
    is_record_type = Property(Entity.from_type_bound.is_record_type)
    is_private = Property(Entity.from_type_bound.is_private)
    root_type = Property(Entity.from_type_bound.root_type)


class SubtypeDecl(BaseSubtypeDecl):
    """
    Subtype declaration.
    """
    subtype = Field(type=T.SubtypeIndication)
    aspects = Field(type=T.AspectSpec)

    @langkit_property(return_type=T.BaseTypeDecl.entity, dynamic_vars=[origin])
    def from_type():
        return Entity.subtype.designated_type.match(
            lambda st=T.SubtypeDecl: st.from_type,
            lambda t: t
        )

    @langkit_property()
    def discrete_range():
        return Entity.subtype.discrete_range

    @langkit_property()
    def xref_equation():
        return Entity.subtype.sub_equation

    is_static_decl = Property(Entity.subtype.is_static_subtype)
    xref_entry_point = Property(True)


@synthetic
class DiscreteBaseSubtypeDecl(BaseSubtypeDecl):
    """
    Specific ``SubtypeDecl`` synthetic subclass for the base type of scalar
    types.
    """
    aspects = NullField()

    is_static_decl = Property(
        True  # TODO: If base subtype is from a formal type, then False
    )

    from_type = Property(
        Self.parent.cast_or_raise(T.BaseTypeDecl).as_entity
    )


class TaskDef(AdaNode):
    """
    Type definition for a task type.
    """
    interfaces = Field(type=T.ParentList)
    public_part = Field(type=T.PublicPart)
    private_part = Field(type=T.PrivatePart)
    end_name = Field(type=T.EndName)


class ProtectedDef(AdaNode):
    """
    Type definition for a protected object.
    """
    public_part = Field(type=T.PublicPart)
    private_part = Field(type=T.PrivatePart)
    end_name = Field(type=T.EndName)


class TaskTypeDecl(BaseTypeDecl):
    """
    Declaration for a task type.
    """
    discriminants = Field(type=T.DiscriminantPart)
    aspects = Field(type=T.AspectSpec)
    definition = Field(type=T.TaskDef)
    is_task_type = Property(True)

    env_spec = EnvSpec(
        add_to_env_kv(Entity.name_symbol, Self),
        add_env()
    )

    defining_env = Property(Entity.children_env)

    discriminants_list = Property(Entity.discriminants.abstract_formal_params)


class SingleTaskTypeDecl(TaskTypeDecl):
    """
    Type declaration for a single task.
    """
    env_spec = EnvSpec(
        # In this case, we don't want to add this type to the env, because it's
        # the single task that contains this type decl that will be added to
        # the env. So we don't call the inherited env spec.
        add_env()
    )


class ProtectedTypeDecl(BaseTypeDecl):
    """
    Declaration for a protected type.
    """
    discriminants = Field(type=T.DiscriminantPart)
    aspects = Field(type=T.AspectSpec)
    interfaces = Field(type=T.ParentList)
    definition = Field(type=T.ProtectedDef)

    discriminants_list = Property(Entity.discriminants.abstract_formal_params)

    defining_env = Property(Entity.children_env)

    next_part_for_decl = Property(Entity.basic_decl_next_part_for_decl)

    env_spec = EnvSpec(
        add_to_env_kv(Entity.name_symbol, Self),
        add_env()
    )


@abstract
class AccessDef(TypeDef):
    """
    Base class for access type definitions.
    """
    has_not_null = Field(type=NotNull)

    is_access_type = Property(True)


class AccessToSubpDef(AccessDef):
    """
    Type definition for accesses to subprograms.
    """
    has_protected = Field(type=Protected)
    subp_spec = Field(type=T.SubpSpec)

    xref_equation = Property(LogicTrue())

    accessed_type = Property(Entity.subp_spec.return_type)

    # We need to add an env to contain the subp_spec's parameters, so that they
    # don't leak in the external scope.
    env_spec = EnvSpec(add_env())


@abstract
class BaseTypeAccessDef(AccessDef):
    """
    Base class for access type definitions.
    """
    pass


class TypeAccessDef(BaseTypeAccessDef):
    """
    Syntactic type definition for accesses.
    """
    has_all = Field(type=All)
    has_constant = Field(type=Constant)
    subtype_indication = Field(type=T.SubtypeIndication)

    accessed_type = Property(Entity.subtype_indication.designated_type)
    xref_equation = Property(Entity.subtype_indication.xref_equation)


@synthetic
class AnonymousTypeAccessDef(BaseTypeAccessDef):
    """
    Synthetic type access, that will directly reference a type decl. It is used
    to generate synthetic anonymous access types.
    """
    type_decl = Field(type=T.BaseTypeDecl)

    accessed_type = Property(Entity.type_decl)


class FormalDiscreteTypeDef(TypeDef):
    """
    Type definition for discrete types in generic formals.
    """
    xref_equation = Property(LogicTrue())

    is_discrete_type = Property(True)


class NullComponentDecl(AdaNode):
    """
    Placeholder for the ``null`` in lists of components.
    """
    pass


class WithClause(AdaNode):
    """
    With clause.
    """
    has_limited = Field(type=Limited)
    has_private = Field(type=Private)
    packages = Field(type=T.Name.list)

    xref_entry_point = Property(True)
    xref_equation = Property(
        Entity.packages.logic_all(lambda p: p.xref_no_overloading)
    )


@abstract
class UseClause(AdaNode):
    """
    Base class for use clauses.
    """
    xref_entry_point = Property(True)


class UsePackageClause(UseClause):
    """
    Use clause for packages.
    """
    packages = Field(type=T.Name.list)

    env_spec = EnvSpec(reference(
        Self.packages.map(lambda n: n.cast(AdaNode)),
        T.Name.use_package_name_designated_env,

        # We don't want to process use clauses that appear in the top-level
        # scope here, as they apply to the library item's environment,
        # which is not processed at this point yet. See CompilationUnit's
        # ref_env_nodes.
        cond=Not(Self.parent.parent.is_a(T.CompilationUnit))
    ))

    @langkit_property(return_type=LexicalEnv.array)
    def designated_envs():
        """
        Return the array of designated envs corresponding to each package name.

        It is very important for this property to be memoized, as it is used a
        lot during lexical environment lookups.
        """
        return Self.packages.map(
            lambda n:
            env.bind(Self.node_env,
                     bind_origin(n, n.as_bare_entity.designated_env))
        )

    xref_equation = Property(
        Entity.packages.logic_all(lambda p: p.xref_no_overloading)
    )


class UseTypeClause(UseClause):
    """
    Use clause for types.
    """
    has_all = Field(type=All)
    types = Field(type=T.Name.list)

    env_spec = EnvSpec(
        handle_children(),
        reference(
            Self.types.map(lambda n: n.cast(AdaNode)),
            T.Name.name_designated_type_env,
            dest_env=Self.node_env,
            # We don't want to process use clauses that appear in the top-level
            # scope here, as they apply to the library item's environment,
            # which is not processed at this point yet. See CompilationUnit's
            # ref_env_nodes.
            cond=Not(Self.parent.parent.is_a(T.CompilationUnit))
        ),
    )

    xref_equation = Property(
        Entity.types.logic_all(lambda p: p.xref_no_overloading)
    )


@abstract
class TypeExpr(AdaNode):
    """
    A type expression is an abstract node that embodies the concept of a
    reference to a type.

    Since Ada has both subtype_indications and anonymous (inline) type
    declarations, a type expression contains one or the other.
    """

    array_ndims = Property(
        bind_origin(Self, Entity.designated_type.array_ndims)
    )

    type_name = Property(
        Entity.cast(T.SubtypeIndication).then(lambda sti: sti.name),
        doc="Return the name node for this type expression, "
        "if applicable, else null",
        public=True
    )

    @langkit_property(dynamic_vars=[origin])
    def accessed_type():
        return Entity.designated_type._.accessed_type

    @langkit_property(dynamic_vars=[origin])
    def defining_env():
        return Entity.designated_type.defining_env

    designated_type = AbstractProperty(
        type=BaseTypeDecl.entity,
        dynamic_vars=[origin],
        doc="""
        Return the type designated by this type expression.
        """
    )

    designated_type_decl = Property(
        bind_origin(Self, Entity.designated_type),
        public=True,
        doc="""
        Returns the type declaration designated by this type expression.
        """
    )

    @langkit_property(return_type=BaseTypeDecl.entity, public=True)
    def designated_type_decl_from(origin_node=T.AdaNode.entity):
        """
        Return the type declaration designated by this type expression as
        viewed from the node given by origin_node.
        """
        return bind_origin(origin_node.node, Entity.designated_type)

    @langkit_property(return_type=BaseTypeDecl.entity, dynamic_vars=[origin])
    def element_type():
        """
        If self is an anonymous access, return the accessed type. Otherwise,
        return the designated type.
        """
        d = Entity.designated_type
        return If(
            d.cast(AnonymousTypeDecl)._.type_def.cast(AccessDef).is_null,
            d,
            Entity.accessed_type,
        )

    @langkit_property(return_type=BaseTypeDecl.entity, dynamic_vars=[origin],
                      warn_on_unused=False)
    def canonical_type():
        return Entity.designated_type._.canonical_type


@synthetic
class EnumLitSynthTypeExpr(TypeExpr):
    """
    Synthetic node. Represents the type expression for an enum literal.
    """
    designated_type = Property(
        Entity.parent.cast(T.EnumLiteralDecl).enum_type
    )

    custom_id_text = Property(
        # The custom_id_text is the combination of the enum type name and of
        # the enum literal name.
        origin.bind(
            Self,
            Entity.designated_type.fully_qualified_name
            .concat(String("."))
            .concat(
                Entity.sym_join(
                    Entity.parent.cast(T.EnumLiteralDecl)
                    .defining_name.as_symbol_array,
                    String("")
                )
            )
        )
    )


class AnonymousType(TypeExpr):
    """
    Container for inline anonymous array and access types declarations.
    """
    type_decl = Field(type=T.AnonymousTypeDecl)

    designated_type = Property(Entity.type_decl)
    xref_equation = Property(Entity.type_decl.sub_equation)

    # TODO: This implementation is not satisfying, because the formatting will
    # be the original source formatting, but will do for the moment.
    # Ideally we would compute a properly formatted version of the anonymous
    # type declaration. Using unparsing in order to avoid duplicating logic
    # between parsing/unparsing.
    custom_id_text = Property(Entity.type_decl.text)


class SubtypeIndication(TypeExpr):
    """
    Reference to a type by name.
    """
    has_not_null = Field(type=NotNull)
    name = Field(type=T.Name)
    constraint = Field(type=T.Constraint)

    # The name for this type has to be evaluated in the context of the
    # SubtypeIndication node itself: we don't want to use whatever lexical
    # environment the caller is using. However we need to inherit the
    # visibility (origin node) of the caller.
    designated_type = Property(
        env.bind(Entity.node_env, Entity.name.designated_type_impl)
    )

    @langkit_property()
    def xref_equation():
        # Called by allocator.xref_equation, since the suffix can be either a
        # qual expr or a subtype indication.
        return And(
            Entity.name.subtype_indication_equation,
            Entity.constraint.then(
                lambda c: c.sub_equation, default_val=LogicTrue()
            )
        )

    @langkit_property()
    def discrete_range():
        rc = Var(Entity.constraint.cast_or_raise(RangeConstraint))
        return rc._.range.range.discrete_range

    @langkit_property(return_type=Bool,
                      dynamic_vars=[default_imprecise_fallback()],
                      public=True)
    def is_static_subtype():
        """
        Returns whether Self denotes a static subtype or not.
        """
        return bind_origin(Self, Entity.constraint.then(
            lambda c: c.is_static,
            default_val=Entity.designated_type.is_static_decl
        ))

    custom_id_text = Property(origin.bind(
        Self,
        Entity.designated_type.fully_qualified_name
    ))


class ConstrainedSubtypeIndication(SubtypeIndication):
    """
    Reference to a type with a range constraint.
    """
    pass


class DiscreteSubtypeIndication(SubtypeIndication):
    """
    Reference to a type with a general constraint.
    """
    pass


class Mode(AdaNode):
    """
    Syntactic indicators for passing modes in formals.
    """
    enum_node = True
    alternatives = ["in", "out", "in_out", "default"]


class ParamSpec(BaseFormalParamDecl):
    """
    Specification for a parameter.
    """
    ids = Field(type=T.DefiningName.list)
    has_aliased = Field(type=Aliased)
    mode = Field(type=Mode)
    type_expr = Field(type=T.TypeExpr)
    default_expr = Field(type=T.Expr)
    aspects = NullField()

    is_mandatory = Property(Self.default_expr.is_null)
    defining_names = Property(Self.ids.map(lambda id: id.as_entity))

    env_spec = EnvSpec(
        add_to_env(env_mappings(Self.ids, Self))
    )

    type_expression = Property(Entity.type_expr)

    @langkit_property()
    def defining_env():
        return Entity.type_expr.defining_env

    @langkit_property()
    def xref_equation():
        typ = Var(Entity.expr_type)
        return (
            Entity.type_expr.sub_equation

            & Entity.default_expr.then(
                lambda de: de.sub_equation
                & Bind(de.type_var, typ,
                       eq_prop=BaseTypeDecl.matching_assign_type),
                default_val=LogicTrue()
            )
        )

    xref_entry_point = Property(True)


class AspectSpec(AdaNode):
    """
    List of aspects in a declaration.
    """
    aspect_assocs = Field(type=T.AspectAssoc.list)


class Overriding(AdaNode):
    """
    Syntactic indicators for subprogram overriding modes.
    """
    enum_node = True
    alternatives = ["overriding", "not_overriding", "unspecified"]


@abstract
class BasicSubpDecl(BasicDecl):
    """
    Base class for subprogram declarations.
    """

    defining_names = Property(Entity.subp_decl_spec.name.as_entity.singleton)

    defining_env = Property(Entity.subp_decl_spec.defining_env)

    type_expression = Property(
        Entity.subp_decl_spec.returns, doc="""
        The expr type of a subprogram declaration is the return type of the
        subprogram if the subprogram is a function.
        """
    )

    @langkit_property()
    def get_body_in_env(env=T.LexicalEnv):
        return (
            env.get(Entity.name_symbol, LK.flat, categories=noprims).find(
                lambda ent:
                # Discard the rebindings of Entity before trying to match
                # against the tentative body, as those do not carry that info.
                ent.node.as_bare_entity.cast(T.BaseSubpBody)._.subp_spec
                .match_signature(Entity.subp_decl_spec.node.as_bare_entity,
                                 True)
            )  # If found, reuse the rebindings of the decl on the body
            .cast(T.BaseSubpBody).node.as_entity
        )

    @langkit_property(return_type=T.BasicDecl.entity)
    def next_part_for_decl():

        decl_scope = Var(Entity.declarative_scope)
        parent_decl = Var(decl_scope.as_entity.then(
            lambda ds: ds.semantic_parent.cast_or_raise(T.BasicDecl)
        ))

        return Cond(
            # Self is a library level subprogram decl. Return the library unit
            # body's root decl.
            Self.parent.cast(T.GenericSubpDecl)
            ._.is_unit_root._or(Self.is_unit_root),

            Entity.defining_name
            .referenced_unit(UnitBody).root
            .get_root_decl.cast(T.BaseSubpBody).as_entity,

            # Self is declared in a private part
            decl_scope.is_a(T.PrivatePart),
            parent_decl.next_part_for_decl.then(
                lambda np: Entity.get_body_in_env(np.children_env)
            ),

            # Self is declared in a public part
            decl_scope.is_a(T.PublicPart),

            # Search in private part
            parent_decl.decl_private_part.then(
                lambda dpp: Entity.get_body_in_env(dpp.children_env),
            )
            # If not found, search in body
            ._or(parent_decl.next_part_for_decl.then(
                lambda np: Entity.get_body_in_env(np.children_env)
            )),

            # No declarative scope: Bail out!
            decl_scope.is_null, No(T.BaseSubpBody.entity),

            # Self is declared in any other declarative scope. Search for decl
            # in it directly.
            Entity.get_body_in_env(decl_scope.children_env)


        )

    @langkit_property()
    def constrain_prefix(prefix=T.Expr):
        return If(
            # If self is a dottable subprogram, then we want to constrain the
            # prefix so that it's type is the type of the first parameter of
            # self.
            Entity.info.md.dottable_subp,
            Bind(prefix.type_var,
                 Entity.subp_decl_spec
                 .unpacked_formal_params.at(0)._.spec.formal_type,
                 eq_prop=BaseTypeDecl.matching_prefix_type),
            LogicTrue()
        )

    @langkit_property()
    def expr_type():
        return Entity.subp_spec_or_null._.return_type

    subp_decl_spec = AbstractProperty(
        type=T.BaseSubpSpec.entity, public=True,
        doc='Return the specification for this subprogram'
    )

    @langkit_property(public=True)
    def body_part():
        """
        Return the BaseSubpBody corresponding to this node.
        """
        return Entity.body_part_for_decl.cast(BaseSubpBody)

    env_spec = EnvSpec(
        # Call the env hook to parse eventual parent unit
        call_env_hook(Self),

        set_initial_env(
            env.bind(Self.initial_env, Entity.decl_scope)
        ),

        add_to_env_kv(
            Entity.name_symbol, Self,
            dest_env=env.bind(
                Self.initial_env,
                Self.as_bare_entity.subp_decl_spec.name.parent_scope
            )
        ),
        add_env(),
        populate_dependent_units(),
        ref_used_packages(),

        handle_children(),

        # Adding subp to the type's environment if the type is tagged and self
        # is a primitive of it.
        add_to_env(
            Self.as_bare_entity.subp_decl_spec.dottable_subp_of.map(
                lambda t: new_env_assoc(
                    key=Entity.name_symbol, val=Self,
                    dest_env=t.children_env,
                    # We pass custom metadata, marking the entity as a dottable
                    # subprogram.
                    metadata=new_metadata(dottable_subp=True)
                )
            ),
        ),

        # Adding subp to the primitives env if the subp is a primitive
        add_to_env(
            Self.as_bare_entity.subp_decl_spec.primitive_subp_of.filtermap(
                lambda t: new_env_assoc(
                    key=Entity.name_symbol, val=Self,
                    dest_env=t.cast_or_raise(T.TypeDecl).primitives,
                    metadata=new_metadata(primitive=t.node)
                ),
                lambda t: t.is_a(T.TypeDecl)
            )
        )
    )


@abstract
class ClassicSubpDecl(BasicSubpDecl):
    """
    This is an intermediate abstract class for subprogram declarations with a
    common structure: overriding indicator, ``SubpSpec``, aspects,
    <other fields>.
    """
    overriding = Field(type=Overriding)
    subp_spec = Field(type=T.SubpSpec)

    subp_decl_spec = Property(Entity.subp_spec)


class SubpDecl(ClassicSubpDecl):
    """
    Regular subprogram declaration.
    """
    aspects = Field(type=T.AspectSpec)


class AbstractSubpDecl(ClassicSubpDecl):
    """
    Declaration for an abstract subprogram.
    """
    aspects = Field(type=T.AspectSpec)


class Pragma(AdaNode):
    """
    Class for pragmas (RM 2.8). Pragmas are compiler directives, that can be
    language or compiler defined.
    """
    id = Field(type=T.Identifier)
    args = Field(type=T.BaseAssoc.list)

    xref_entry_point = Property(True)

    @langkit_property()
    def xref_equation():
        return Cond(
            Or(
                Entity.id.name_is('Assert'),
                Entity.id.name_is('Compile_Time_Warning'),
                Entity.id.name_is('Compile_Time_Error'),
            ),
            Let(lambda expr=Entity.args.at(0).assoc_expr:
                expr.sub_equation & bool_bind(expr.type_var)),

            Entity.id.name_is('Unreferenced'),
            Entity.args.logic_all(
                lambda assoc:
                assoc.assoc_expr.cast_or_raise(T.Name).xref_no_overloading
            ),

            Entity.id.name_symbol.any_of(
                'Import', 'Export', 'Interface', 'Convention',
                'Pack', 'Pure', 'Preelaborate', 'Elaborate_Body',
                'Inline'
            ),
            Entity.associated_entity_name.then(
                lambda n: n.xref_no_overloading, default_val=LogicTrue()
            ),

            Entity.id.name_is('Warnings'),
            If(And(Entity.args.length == 2,
                   Entity.args.at(1).assoc_expr.is_a(T.BaseId)),
               Entity.args.at(1).assoc_expr.cast(T.BaseId).xref_equation,
               LogicTrue()),

            LogicTrue(),
        )

    @langkit_property()
    def associated_entity_name():
        return Cond(
            Entity.id.name_symbol.any_of(
                'Import', 'Export', 'Interface', 'Convention'
            ),
            Entity.args.at(1).assoc_expr.cast_or_raise(T.Name),
            Entity.id.name_symbol.any_of(
                'Pack', 'Pure', 'Preelaborate', 'Elaborate_Body', 'Inline'
            ),
            Entity.args.at(0)._.assoc_expr.cast(T.BaseId),

            No(T.BaseId.entity),
        )

    @langkit_property()
    def associated_decls_helper():
        return Entity.associated_entity_name.then(
            # Find the current declarative scope
            lambda name: Entity.declarative_scope.then(
                # Get entities in it
                lambda decl_scope: decl_scope.as_entity.children_env.get(
                    name.name_symbol, lookup=LK.flat, categories=noprims
                )
            )
            # Only get entities that are after self in the source
            .filtermap(lambda ent: ent.cast(T.BasicDecl),
                       lambda ent: ent.node < Self)
        )

    @langkit_property(public=True)
    def associated_decls():
        """
        Return an array of ``BasicDecl`` instances associated with this pragma,
        or an empty array if non applicable.
        """
        top_level_decl = Var(Self.parent.parent.cast(T.CompilationUnit).then(
            lambda cu: cu.body.cast_or_raise(T.LibraryItem)
            .item.as_entity.singleton,
            default_val=No(BasicDecl.entity.array)
        ))

        enclosing_program_unit = Var(Self.parents.find(
            lambda p: p.is_a(T.BasicDecl)
        ).cast(T.BasicDecl).as_entity)

        # TODO: This should be using a ._or, but is waiting on a fix for
        # R903-028.

        # NOTE: The whole reason we have to implement custom resolution for
        # decls associated to a pragma, is because there can be several
        # associated decls, so the regular crossref mechanism is not
        # sufficient, as in the following example::
        #
        #     procedure Foo;
        #     procedure Foo (A : Integer);
        #     pragma Inline (Foo);
        return Entity.associated_entity_name.then(lambda name: Let(
            lambda p=Entity.associated_decls_helper._or(top_level_decl): If(
                Not(p.equals(No(T.BasicDecl.entity.array))),
                p,
                enclosing_program_unit.then(lambda epu: If(
                    epu.defining_name.name_matches(name),
                    epu.singleton,
                    No(BasicDecl.entity.array)
                ), default_val=top_level_decl)
            )
        ),
            # If no name, then program unit pragma necessarily
            default_val=enclosing_program_unit.singleton
        )


class PragmaArgumentAssoc(BaseAssoc):
    """
    Argument assocation in a pragma.
    """
    id = Field(type=T.Identifier)
    expr = Field(type=T.Expr)
    assoc_expr = Property(Entity.expr)


@abstract
class AspectClause(AdaNode):
    """
    Base class for aspect clauses.
    """
    xref_entry_point = Property(True)
    xref_equation = Property(LogicTrue())


class EnumRepClause(AspectClause):
    """
    Representation clause for enumeration types.
    """
    type_name = Field(type=T.Name)
    aggregate = Field(type=T.BaseAggregate)


class AttributeDefClause(AspectClause):
    """
    Clause for an attribute definition (``for ...'Attribute use ...;``).
    """
    attribute_expr = Field(type=T.Name)
    expr = Field(type=T.Expr)

    xref_entry_point = Property(True)

    @langkit_property()
    def xref_equation():
        attr = Var(Entity.attribute_expr.cast_or_raise(T.AttributeRef))
        rel_name = Var(attr.attribute.name_symbol)

        return Cond(
            rel_name.any_of('Read', 'Write', 'Input', 'Output'),
            Entity.expr.cast_or_raise(T.Name).xref_no_overloading
            & Predicate(
                BasicDecl.is_stream_subprogram_for_type,
                Entity.expr.cast(T.Name).ref_var,
                attr.prefix.name_designated_type,
                rel_name == 'Input',
            ),

            Entity.expr.sub_equation
        ) & attr.then(
            lambda ar: ar.prefix.sub_equation, default_val=LogicTrue()
        )


class ComponentClause(AdaNode):
    """
    Representation clause for a single component.
    """
    id = Field(type=T.Identifier)
    position = Field(type=T.Expr)
    range = Field(type=T.RangeSpec)

    xref_entry_point = Property(True)

    @langkit_property()
    def xref_equation():
        # Find the record representation clause in which the component clause
        # appears.
        rep_clause = Var(Entity.parent.parent.cast_or_raise(T.RecordRepClause))

        # rep_clause.name must refer to a subtype, so it's safe to use
        # designated_env_no_overloading.
        record_env = Var(rep_clause.name.designated_env_no_overloading)

        return And(
            # Resolve `id` in the environment of the original record
            env.bind(record_env, Entity.id.xref_equation),
            Entity.position.sub_equation,
            Entity.range.xref_equation
        )


class RecordRepClause(AspectClause):
    """
    Representation clause for a record type.
    """
    name = Field(type=T.Name)
    at_expr = Field(type=T.Expr)
    components = Field(type=T.AdaNode.list)

    @langkit_property()
    def xref_equation():
        return And(
            Entity.name.xref_no_overloading,
            Entity.at_expr.then(
                lambda e: e.sub_equation,
                default_val=LogicTrue()
            )
        )


class AtClause(AspectClause):
    """
    Representation clause (``for .. use at ...;``).
    """
    name = Field(type=T.BaseId)
    expr = Field(type=T.Expr)


class SingleTaskDecl(BasicDecl):
    """
    Declaration for a single task.
    """
    task_type = Field(type=T.SingleTaskTypeDecl)
    aspects = NullField()

    defining_names = Property(Entity.task_type.defining_names)
    expr_type = Property(Entity.task_type)

    env_spec = EnvSpec(add_to_env_kv(Self.name_symbol, Self))


class SingleProtectedDecl(BasicDecl):
    """
    Declaration for a single protected object.
    """
    name = Field(type=T.DefiningName)
    aspects = Field(type=T.AspectSpec)
    interfaces = Field(type=T.ParentList)
    definition = Field(type=T.ProtectedDef)

    defining_names = Property(Entity.name.singleton)

    defining_env = Property(Entity.children_env)

    env_spec = EnvSpec(
        add_to_env_kv(Entity.name_symbol, Self),
        add_env()
    )


class AspectAssoc(AdaNode):
    """
    Name/expression association in an aspect.
    """
    id = Field(type=T.Name)
    expr = Field(type=T.Expr)

    xref_entry_point = Property(True)

    @langkit_property()
    def xref_equation():
        target = Var(Self.parent.parent.parent)
        return Cond(
            # Iterable aspect
            Entity.id.name_is('Iterable'),
            Entity.expr.cast(T.Aggregate).assocs.unpacked_params.logic_all(
                lambda sa:
                sa.assoc.expr
                .cast_or_raise(T.Name).xref_no_overloading(sequential=False)
            ),

            # Contracts
            target.is_a(BasicSubpDecl, BaseSubpBody)
            & Entity.id.name_symbol.any_of(
                'Pre', 'Post', 'Model_Pre', 'Model_Post'
            ),

            Entity.expr.sub_equation
            & bool_bind(Self.expr.type_var),

            # Model_Of aspect on types
            target.is_a(T.BaseTypeDecl)
            & Entity.id.name_symbol.any_of('Model_Of'),
            Bind(Self.expr.cast_or_raise(T.Name).ref_var,
                 Entity.expr.cast_or_raise(T.Name).name_designated_type),

            # Model_Of aspect on subprograms
            target.is_a(T.BasicSubpDecl)
            & Entity.id.name_symbol.any_of('Model_Of'),
            Bind(
                Self.expr.cast_or_raise(T.Name).ref_var,
                Entity.expr.cast_or_raise(T.Name).all_env_elements.find(
                    lambda e:
                    e.is_a(T.BasicSubpDecl)
                    & e.cast(T.BasicSubpDecl).subp_decl_spec
                    .match_signature(
                        target.as_entity.cast_or_raise(T.BasicSubpDecl)
                        .subp_decl_spec,
                        False
                    )
                )
            ),

            LogicTrue()
        )

    @langkit_property(return_type=T.String)
    def aspect_name(n=T.Name.entity):
        """
        Return the string representation of the given name, which must be a
        Name that can appear in an aspect association id.
        """
        # TODO: would be cleaner to implement a general "image" function in
        # class Name directly.
        return n.match(
            lambda bid=T.BaseId: bid.sym.image,
            lambda ar=T.AttributeRef: Self.aspect_name(ar.prefix)
                                          .concat(String("'"))
                                          .concat(ar.attribute.sym.image),
            lambda _: PropertyError(
                T.String,
                "aspect_name called on an invalid aspect name"
            ),
        )


class NumberDecl(BasicDecl):
    """
    Declaration for a static constant number.
    """
    ids = Field(type=T.DefiningName.list)
    expr = Field(type=T.Expr)
    aspects = NullField()

    defining_names = Property(Entity.ids.map(lambda id: id))

    env_spec = EnvSpec(add_to_env(env_mappings(Self.ids, Self)))

    @langkit_property(call_memoizable=True)
    def expr_type():
        p = Var(If(Self.expr.type_val.is_null,
                   Entity.expr.resolve_names,
                   True))

        typ = Var(If(p,
                     Self.expr.type_val.cast_or_raise(BaseTypeDecl.entity),
                     No(BaseTypeDecl.entity)))

        return If(typ.is_int_type,
                  Self.universal_int_type,
                  Self.universal_real_type).cast(BaseTypeDecl.entity)

    xref_entry_point = Property(True)

    is_static_decl = Property(True)

    xref_equation = Property(Entity.expr.sub_equation)


class ObjectDecl(BasicDecl):
    """
    Base class for Ada object declarations (RM 3.3.1). Ada object declarations
    are variables/constants declarations that can be declared in any
    declarative scope.
    """

    ids = Field(type=T.DefiningName.list)
    has_aliased = Field(type=Aliased)
    has_constant = Field(type=Constant)
    mode = Field(type=Mode)
    type_expr = Field(type=T.TypeExpr)
    default_expr = Field(type=T.Expr)
    renaming_clause = Field(type=T.RenamingClause)
    aspects = Field(type=T.AspectSpec)

    env_spec = EnvSpec(add_to_env(env_mappings(Self.ids, Self)))

    defining_names = Property(Entity.ids.map(lambda id: id))
    defining_env = Property(Entity.type_expr.defining_env)
    type_expression = Property(Entity.type_expr)

    @langkit_property(public=True, return_type=Bool)
    def is_static_decl():
        return Self.has_constant.as_bool & Entity.default_expr.then(
            lambda expr: expr.is_static_expr
        )

    @langkit_property()
    def xref_equation():
        typ = Var(Entity.expr_type)
        return (
            Entity.type_expr.sub_equation
            & Entity.default_expr.then(
                lambda de:
                de.sub_equation
                & Bind(de.node.type_var,
                       typ,
                       eq_prop=BaseTypeDecl.matching_assign_type),
                default_val=LogicTrue()
            )
            & Entity.renaming_clause.then(
                lambda rc:
                rc.renamed_object.sub_equation
                & Bind(rc.renamed_object.node.type_var, typ,
                       eq_prop=BaseTypeDecl.matching_assign_type),
                default_val=LogicTrue()
            )
        )

    @langkit_property(public=True)
    def public_part_decl():
        """
        If this object decl is the constant completion of an object decl in the
        public part, return the object decl from the public part.
        """

        return If(
            Entity.is_in_private_part & Self.has_constant.as_bool,
            Self.declarative_scope.parent
            .cast(T.BasePackageDecl).public_part.children_env
            .get_first(Self.name_symbol, LK.flat, categories=noprims)
            .cast(T.BasicDecl),
            No(T.BasicDecl.entity)
        )

    xref_entry_point = Property(True)


class ExtendedReturnStmtObjectDecl(ObjectDecl):
    """
    Object declaration that is part of an extended return statement.
    """
    pass


class DeclarativePart(AdaNode):
    """
    List of declarations.
    """
    annotations = Annotations(snaps=True)

    decls = Field(type=T.AdaNode.list)

    @langkit_property(memoized=True)
    def types_with_models():
        return Self.as_bare_entity.decls.filtermap(
            lambda d: d.cast(T.BaseTypeDecl),
            lambda d:
            Not(d.cast(T.BaseTypeDecl)._.get_aspect_expr('Model_Of').is_null)
        )

    @langkit_property()
    def use_clauses_envs():
        """
        Returns the envs for all the use clauses declared in this declarative
        part.
        """
        return Entity.decls.children.filtermap(
            lambda u: u.cast(T.UseClause).match(
                lambda upc=T.UsePackageClause: upc.designated_envs.env_group(),
                lambda utc=T.UseTypeClause:
                utc.types.map(lambda n: n.name_designated_type_env).env_group()
            ),
            lambda c: c.is_a(T.UseClause)
        ).env_group()


class PrivatePart(DeclarativePart):
    """
    List of declarations in a private part.
    """
    env_spec = EnvSpec(
        add_to_env_kv('__privatepart', Self),
        add_env(transitive_parent=True)
    )


class PublicPart(DeclarativePart):
    """
    List of declarations in a public part.
    """
    pass


@abstract
class BasePackageDecl(BasicDecl):
    """
    Package declarations. Concrete instances of this class
    will be created in generic package declarations. Other non-generic
    package declarations will be instances of PackageDecl.

    The behavior is the same, the only difference is that BasePackageDecl
    and PackageDecl have different behavior regarding lexical environments.
    In the case of generic package declarations, we use BasePackageDecl
    which has no env_spec, and the environment behavior is handled by the
    GenericPackageDecl instance.
    """
    package_name = Field(type=T.DefiningName)
    aspects = Field(type=T.AspectSpec)
    public_part = Field(type=T.PublicPart)
    private_part = Field(type=T.PrivatePart)
    end_name = Field(type=T.EndName)

    defining_names = Property(Entity.package_name.singleton)
    defining_env = Property(Entity.children_env)

    @langkit_property(public=True)
    def body_part():
        """
        Return the PackageBody corresponding to this node.
        """
        return Entity.body_part_for_decl.cast(T.PackageBody)

    declarative_region = Property(Entity.public_part)


class PackageDecl(BasePackageDecl):
    """
    Non-generic package declarations.
    """
    env_spec = child_unit(
        Entity.name_symbol, Entity.decl_scope,
        dest_env=env.bind(Self.parent.node_env, Entity.decl_scope(False))
    )


class ExceptionDecl(BasicDecl):
    """
    Exception declarations.
    """
    ids = Field(type=T.DefiningName.list)
    renames = Field(type=T.RenamingClause)
    aspects = Field(type=T.AspectSpec)
    defining_names = Property(Entity.ids.map(lambda id: id))

    @langkit_property()
    def next_part_for_decl():
        """
        An exception declaration never has a next part.
        """
        return No(BasicDecl.entity)

    env_spec = EnvSpec(add_to_env(env_mappings(Self.ids, Self)))


@abstract
class GenericInstantiation(BasicDecl):
    """
    Instantiations of generics.
    """

    inst_env = UserField(type=T.LexicalEnv, public=False)

    @langkit_property(external=True, uses_entity_info=False, uses_envs=True,
                      return_type=LexicalEnv)
    def instantiation_env():
        pass

    generic_entity_name = AbstractProperty(
        type=T.Name.entity, doc="""
        Return the name of the generic entity designated by this generic
        instantiation.
        """
    )

    generic_inst_params = AbstractProperty(
        type=T.AssocList.entity, doc="""
        Return the parameters of this generic instantiation
        """
    )

    is_any_formal = Property(
        Entity.generic_inst_params._.at(0)._.expr._.is_a(T.BoxExpr)
    )

    nonbound_generic_decl = Property(
        Self.as_bare_entity.generic_entity_name
        .all_env_elements(seq=True, seq_from=Self).at(0)
        ._.match(
            lambda b=Body: b.decl_part,
            lambda rd=T.GenericRenamingDecl: rd.resolve,
            lambda d=BasicDecl: d,
            lambda _: No(T.GenericDecl.entity)
        )._.cast(T.GenericDecl),
        doc="""
        Return the formal package designated by the right hand part of this
        generic package instantiation.
        """
    )

    designated_generic_decl = AbstractProperty(
        type=T.BasicDecl.entity, public=True, doc="""
        Return the generic decl entity designated by this instantiation,
        containing the generic context. This is equivalent to the expanded
        generic unit in GNAT.
        """
    )

    xref_entry_point = Property(True)

    xref_equation = Property(
        Bind(Entity.generic_entity_name.ref_var,
             Entity.nonbound_generic_decl)
        & Entity.generic_entity_name.match(
            lambda dn=T.DottedName: dn.prefix.xref_no_overloading,
            lambda _: LogicTrue()
        ) & If(
            Entity.is_any_formal,
            LogicTrue(),

            Entity.designated_generic_decl.cast_or_raise(T.GenericDecl)
            ._.formal_part.match_param_list(
                Entity.generic_inst_params, False
            ).logic_all(lambda pm: Let(
                lambda actual_name=pm.actual.assoc.expr.cast(T.Name):
                pm.formal.spec.cast(T.GenericFormal).decl.match(
                    lambda _=T.TypeDecl: actual_name.xref_no_overloading,

                    lambda subp_decl=T.FormalSubpDecl.entity:
                    Or(
                        actual_name.xref_no_overloading(all_els=True)
                        & Predicate(BasicDecl.subp_decl_match_signature,
                                    actual_name.ref_var,
                                    subp_decl.cast(T.BasicDecl)),
                        LogicTrue()
                    ),

                    lambda obj_decl=T.ObjectDecl:
                    pm.actual.assoc.expr.sub_equation
                    & TypeBind(pm.actual.assoc.expr.type_var,
                               obj_decl.expr_type),

                    lambda _: LogicTrue(),
                ) & pm.actual.name.then(
                    lambda n:
                    Bind(n.ref_var,
                         pm.formal.name.node.as_bare_entity.basic_decl),
                    default_val=LogicTrue()
                )
            ))
        )
    )


class GenericSubpInstantiation(GenericInstantiation):
    """
    Instantiations of a generic subprogram.
    """

    overriding = Field(type=Overriding)
    kind = Field(type=T.SubpKind)
    subp_name = Field(type=T.DefiningName)
    generic_subp_name = Field(type=T.Name)
    params = Field(type=T.AssocList)
    aspects = Field(type=T.AspectSpec)

    defining_names = Property(Entity.subp_name.singleton)

    generic_entity_name = Property(Entity.generic_subp_name)
    generic_inst_params = Property(Entity.params)

    @langkit_property(public=True)
    def designated_subp():
        """
        Return the subprogram decl designated by this instantiation.
        """
        return Self.nonbound_generic_decl.then(
            lambda p: BasicSubpDecl.entity.new(
                node=p.node.cast(GenericSubpDecl).subp_decl,
                info=T.entity_info.new(
                    md=p.info.md,
                    rebindings=Entity.info.rebindings
                    # Append the rebindings from the decl
                    .concat_rebindings(p._.decl.info.rebindings)
                    .append_rebinding(
                        p.node.children_env, Self.instantiation_env
                    ),
                    from_rebound=p.info.from_rebound
                )
            ).cast(T.entity)
        )

    designated_generic_decl = Property(
        Entity.designated_subp.parent.cast(T.BasicDecl)
    )

    env_spec = EnvSpec(
        call_env_hook(Self),

        set_initial_env(
            env.bind(Self.initial_env, Let(
                lambda scope=Self.as_bare_entity.defining_name.parent_scope:
                If(scope == EmptyEnv, env, scope)
            ))
        ),

        add_env(),
        populate_dependent_units(),
        ref_used_packages(),

        handle_children(),
        add_to_env(
            env.bind(
                Self.initial_env,
                Self.nonbound_generic_decl._.formal_part.match_param_list(
                    Entity.params, False
                ).map(lambda pm: new_env_assoc(
                    key=pm.formal.name.name_symbol,
                    val=pm.actual.assoc.expr.node,
                    dest_env=Self.instantiation_env
                ))
            ),
            resolver=AdaNode.resolve_generic_actual,
        ),
        add_to_env_kv(
            Entity.name_symbol, Self,
            resolver=T.GenericSubpInstantiation.designated_subp,
            dest_env=Self.node_env
        ),
    )


class GenericPackageInstantiation(GenericInstantiation):
    """
    Instantiations of a generic package.
    """

    name = Field(type=T.DefiningName)
    generic_pkg_name = Field(type=T.Name)
    params = Field(type=T.AssocList)
    aspects = Field(type=T.AspectSpec)

    generic_entity_name = Property(Entity.generic_pkg_name)
    generic_inst_params = Property(Entity.params)

    @langkit_property()
    def designated_package():
        return Self.nonbound_generic_decl.then(
            lambda p: BasePackageDecl.entity.new(
                node=p.node.cast(GenericPackageDecl).package_decl,
                info=T.entity_info.new(
                    md=p.info.md,

                    # Take the rebindings from the current context
                    rebindings=Entity.info.rebindings

                    # Append the rebindings from the decl
                    .concat_rebindings(p._.decl.info.rebindings)

                    # Append the rebindings for the current instantiation.
                    # NOTE: We use the formal env to create rebindings. There,
                    # we purposefully want the children env of the P node, with
                    # no rebindings associated, since the rebinding indication
                    # concerns the *naked* generic. Hence we use
                    # p.node.children_env.
                    .append_rebinding(p.node.children_env,
                                      Self.instantiation_env),
                    from_rebound=p.info.from_rebound
                ),
            )
        )

    designated_generic_decl = Property(
        Entity.designated_package.parent.cast(T.BasicDecl)
    )

    @langkit_property(return_type=LexicalEnv, dynamic_vars=[origin])
    def defining_env_impl(inst_from_formal=(Bool, False)):
        """
        Specialized function for getting the defining env for this generic
        instantiation.

        If ``inst_from_formal`` is True, we know that this generic package
        instantiation is coming from a rebound formal package, and that we need
        visibility on the formals.
        """
        dp = Var(Entity.designated_package)
        return Array([
            If(
                Self.is_formal_pkg | inst_from_formal,
                Array([dp.children_env, dp.parent.children_env]).env_group(),
                dp.children_env
            ),
            # The environment of the instantiation needs to be available,
            # because library unit generic package instantiations can be
            # nested, and so need to be available, such as in::
            #
            #     --  a.ads
            #     package A is new Gen_A;
            #
            #     --  a-b.ads
            #     package A.B is new A.Gen_B;
            #
            Entity.children_env
        ]).env_group()

    @langkit_property(return_type=LexicalEnv)
    def defining_env():
        return Entity.defining_env_impl

    defining_names = Property(Entity.name.singleton)

    is_formal_pkg = Property(Self.parent.is_a(T.GenericFormalPackage))

    env_spec = EnvSpec(
        call_env_hook(Self),

        set_initial_env(env.bind(
            Self.initial_env,
            If(Self.is_formal_pkg, Self.initial_env, Entity.decl_scope(False))
        )),

        add_to_env_kv(Entity.name_symbol, Self),
        add_env(),
        populate_dependent_units(),
        ref_used_packages(),

        handle_children(),
        add_to_env(
            env.bind(
                Self.initial_env,
                If(Entity.is_any_formal,
                   No(T.env_assoc.array),
                   Self.nonbound_generic_decl._.formal_part.match_param_list(
                       Entity.params, False
                   ).map(lambda pm: new_env_assoc(
                       key=pm.formal.name.name_symbol,
                       val=pm.actual.assoc.expr.node,
                       dest_env=Self.instantiation_env
                   )))
            ),
            resolver=AdaNode.resolve_generic_actual,
        )
    )


class RenamingClause(AdaNode):
    """
    Renaming clause, used everywhere renamings are valid.
    """
    renamed_object = Field(type=T.Name)


class PackageRenamingDecl(BasicDecl):
    """
    Declaration for a package renaming.
    """

    name = Field(type=T.DefiningName)
    renames = Field(type=RenamingClause)
    aspects = Field(type=T.AspectSpec)

    @langkit_property(return_type=T.BasicDecl.entity, public=True)
    def renamed_package():
        """
        Return the declaration of the package that is renamed by Self.
        """
        return env.bind(
            Entity.node_env,
            Entity.renames.renamed_object.env_elements.at(0)._.cast(BasicDecl)
        )

    @langkit_property(return_type=T.BasicDecl.entity, public=True)
    def final_renamed_package():
        """
        Return the declaration of the package that is ultimately renamed by
        Self, skipping through all intermediate package renamings.
        """
        pkg = Var(Entity.renamed_package)
        return pkg.cast(PackageRenamingDecl).then(
            lambda r: r.final_renamed_package,
            default_val=pkg
        )

    env_spec = child_unit(Entity.name_symbol, Self.name.parent_scope)

    defining_names = Property(Entity.name.singleton)
    defining_env = Property(Entity.renamed_package.defining_env)

    xref_entry_point = Property(True)
    xref_equation = Property(
        Entity.renames.renamed_object.xref_no_overloading
    )


@abstract
class GenericRenamingDecl(BasicDecl):
    """
    Base node for all generic renaming declarations.
    """
    renaming_name = AbstractProperty(type=T.Name.entity)

    resolve = Property(env.bind(
        Entity.node_env,
        Entity.renaming_name.env_elements.at(0)._.match(
            lambda gd=T.GenericDecl: gd,
            lambda grd=T.GenericRenamingDecl: grd.resolve,
            lambda _: No(T.GenericDecl.entity)
        )
    ), type=T.GenericDecl.entity, doc="""
    Resolve the GenericDecl this renaming decl is pointing at
    """)


class GenericPackageRenamingDecl(GenericRenamingDecl):
    """
    Declaration for a generic package renaming.
    """
    name = Field(type=T.DefiningName)
    renames = Field(type=T.Name)
    aspects = Field(type=T.AspectSpec)

    defining_names = Property(Entity.name.singleton)
    defining_env = Property(Entity.resolve.defining_env)
    renaming_name = Property(Entity.renames)

    env_spec = child_unit(Entity.name_symbol, Self.name.parent_scope)


class SubpKind(AdaNode):
    """
    Qualifier for a subprogram kind.
    """
    enum_node = True
    alternatives = ["procedure", "function"]


class GenericSubpRenamingDecl(GenericRenamingDecl):
    """
    Declaration for a generic subprogram renaming.
    """
    env_spec = child_unit(Entity.name_symbol, Self.name.parent_scope)

    kind = Field(type=T.SubpKind)
    name = Field(type=T.DefiningName)
    renames = Field(type=T.Name)
    aspects = Field(type=T.AspectSpec)

    defining_names = Property(Entity.name.singleton)
    renaming_name = Property(Entity.renames)


@abstract
class FormalSubpDecl(ClassicSubpDecl):
    """
    Formal subprogram declarations, in generic declarations formal parts.
    """
    default_expr = Field(type=T.Expr)
    aspects = Field(type=T.AspectSpec)

    defining_names = Property(Entity.subp_spec.name.as_entity.singleton)


class ConcreteFormalSubpDecl(FormalSubpDecl):
    """
    Formal declaration for a concrete subprogram.
    """

    pass


class AbstractFormalSubpDecl(FormalSubpDecl):
    """
    Formal declaration for an abstract subprogram.
    """

    pass


class GenericFormalPart(BaseFormalParamHolder):
    """
    List of declaration for generic formals.
    """
    decls = Field(type=T.AdaNode.list)

    abstract_formal_params = Property(Entity.decls.keep(BaseFormalParamDecl))


@abstract
class GenericFormal(BaseFormalParamDecl):
    """
    Declaration for a generic formal.
    """
    decl = Field(T.BasicDecl)
    aspects = NullField()
    defining_names = Property(Entity.decl.defining_names)


class GenericFormalObjDecl(GenericFormal):
    """
    Formal declaration for an object.
    """

    pass


class GenericFormalTypeDecl(GenericFormal):
    """
    Formal declaration for a type.
    """

    pass


class GenericFormalSubpDecl(GenericFormal):
    """
    Formal declaration for a subprogram.
    """

    pass


class GenericFormalPackage(GenericFormal):
    """
    Formal declaration for a package.
    """

    pass


class GenericSubpInternal(BasicSubpDecl):
    """
    Internal node for generic subprograms.
    """
    subp_spec = Field(type=T.SubpSpec)
    aspects = Field(type=T.AspectSpec)

    subp_decl_spec = Property(Entity.subp_spec)
    env_spec = EnvSpec(add_env())


@abstract
class GenericDecl(BasicDecl):
    """
    Base class for generic declarations.
    """
    formal_part = Field(type=T.GenericFormalPart)
    decl = AbstractProperty(type=T.BasicDecl.entity)

    annotations = Annotations(rebindable=True)


class GenericSubpDecl(GenericDecl):
    """
    Generic subprogram declaration.
    """
    env_spec = child_unit(Entity.name_symbol,
                          Self.subp_decl.subp_spec.name.parent_scope)

    subp_decl = Field(type=T.GenericSubpInternal)
    aspects = NullField()

    defining_names = Property(
        Entity.subp_decl.subp_spec.name.as_entity.singleton)

    @langkit_property(public=True)
    def body_part():
        """
        Return the BaseSubpBody corresponding to this node.
        """
        return Entity.body_part_for_decl.cast(BaseSubpBody)

    env_spec = EnvSpec(
        # Process eventual parent unit
        call_env_hook(Self),

        set_initial_env(
            env.bind(Self.initial_env, Entity.defining_name.parent_scope)
        ),
        add_to_env_kv(Entity.name_symbol, Self),
        add_env(),
        populate_dependent_units(),
        ref_used_packages(),
    )

    decl = Property(Entity.subp_decl)

    # Overriding properties forwarding to internal subp decl
    is_imported = Property(Entity.subp_decl.is_imported)
    next_part_for_decl = Property(Entity.subp_decl.next_part_for_decl)


class GenericPackageInternal(BasePackageDecl):
    """
    This class denotes the internal package contained by a GenericPackageDecl.
    """
    # Implementation note: This exists so that we can insert an environment to
    # distinguish between formal parameters and the package's contents.

    env_spec = EnvSpec(add_env())


class GenericPackageDecl(GenericDecl):
    """
    Generic package declaration.
    """
    env_spec = child_unit(
        Entity.name_symbol,
        Entity.decl_scope,
        dest_env=env.bind(Self.parent.node_env, Entity.decl_scope(False))
    )

    package_decl = Field(type=GenericPackageInternal)
    aspects = NullField()

    defining_env = Property(Entity.package_decl.defining_env)

    defining_names = Property(
        Self.package_decl.package_name.as_entity.singleton)

    @langkit_property(public=True)
    def body_part():
        """
        Return the PackageBody corresponding to this node, or null if there is
        none.
        """
        return Entity.package_decl.body_part

    decl = Property(Entity.package_decl)


@abstract
@has_abstract_list
class Expr(AdaNode):
    """
    Base class for expressions.
    """

    type_var = UserField(LogicVar, public=False)
    type_val = Property(Self.type_var.get_value)

    expression_type = Property(
        Self.logic_val(Entity, Self.type_var)
            .value.cast_or_raise(T.BaseTypeDecl),
        public=True,
        doc="""
        Return the declaration corresponding to the type of this expression
        after name resolution.
        """
    )

    @langkit_property(public=True, return_type=Bool,
                      dynamic_vars=[default_imprecise_fallback()])
    def is_static_expr():
        """
        Return whether this expression is static according to the ARM
        definition of static. See RM 4.9.
        """
        return bind_origin(Self, Entity.match(
            lambda _=NumLiteral: True,
            lambda _=StringLiteral: True,
            lambda ar=AttributeRef: Or(

                Not(ar.prefix.name_designated_type
                    ._.root_type._.is_formal_type)
                & (ar.attribute.name_symbol == 'Base'),

                ar.prefix.name_designated_type
                ._.is_static_decl & ar.attribute.name_symbol.any_of(
                    'First', 'Last'
                ),
                ar.prefix.referenced_decl._.is_array
                & ar.attribute.name_symbol.any_of(
                    'First', 'Last', 'Length'
                ) & ar.args_list._.at(0).expr.is_static_expr
            ),
            lambda ce=CallExpr:
            ce.name.name_designated_type._.is_static_decl
            & ce.params.at(0).expr.is_static_expr,
            lambda qe=QualExpr:
            qe.prefix.name_designated_type._.is_static_decl
            & qe.suffix.is_static_expr,
            lambda n=Name: n.referenced_decl.is_static_decl,
            lambda me=MembershipExpr:
            me.expr.is_static_expr & me.membership_exprs.all(
                lambda e: e.is_static_expr
            ),
            lambda bo=BinOp:
            bo.left.is_static_expr
            & bo.right.is_static_expr
            & bo.op.referenced_decl.is_null,
            lambda uo=UnOp:
            uo.expr.is_static_expr
            & uo.op.referenced_decl.is_null,
            lambda i=IfExpr:
            i.cond_expr.is_static_expr & i.then_expr.is_static_expr
            & i.alternatives.all(
                lambda a:
                a.cond_expr.is_static_expr
                & a.then_expr.is_static_expr
            )
            & i.else_expr.is_static_expr,
            lambda pe=ParenExpr: pe.expr.is_static_expr,
            lambda _: False
        ))

    @langkit_property(public=True)
    def first_corresponding_decl():
        """
        Return the first decl that is lexically named like self in self's
        scope.
        """
        return No(T.BasicDecl.entity)

    @langkit_property(external=True, uses_entity_info=False, uses_envs=False,
                      return_type=T.BigInt, public=True)
    def eval_as_int():
        """
        Statically evaluates self, and returns the value of the evaluation as
        an integer.
        """
        pass

    @langkit_property(return_type=DiscreteRange)
    def discrete_range():
        """
        Return the discrete range for this expression, if applicable.
        """
        return Entity.match(
            # TODO: This won't handle array objects
            lambda ar=T.AttributeRef: ar.prefix.discrete_range,

            lambda n=T.Name: n.name_designated_type.then(
                lambda dt: dt.discrete_range
            ),

            lambda bo=T.BinOp: DiscreteRange.new(
                low_bound=bo.left.eval_as_int, high_bound=bo.right.eval_as_int
            ),
            lambda _: No(DiscreteRange)
        )

    @langkit_property(return_type=LexicalEnv, dynamic_vars=[env, origin])
    def designated_env_no_overloading():
        """
        Returns the lexical environment designated by this name, assuming
        that this name cannot be overloaded.
        """
        return Entity.designated_env

    @langkit_property(kind=AbstractKind.abstract_runtime_check,
                      return_type=LexicalEnv, dynamic_vars=[env, origin])
    def designated_env():
        """
        Returns the lexical environment designated by this name.

        If this name involves overloading, this will return a combination of
        the various candidate lexical environments.
        """
        pass

    env_elements = Property(
        Entity.env_elements_impl.filter(lambda e: Self.has_visibility(e)),
        dynamic_vars=[env]
    )

    @langkit_property(return_type=AdaNode.entity.array,
                      kind=AbstractKind.abstract_runtime_check,
                      dynamic_vars=[env])
    def env_elements_impl():
        """
        Returns the list of annotated elements in the lexical environment
        that can statically be a match for expr before overloading analysis.
        """
        pass

    @langkit_property(return_type=AdaNode.entity.array, public=True)
    def matching_nodes():
        """
        Return the list of AST nodes that can be a match for this expression
        before overloading analysis.
        """
        return env.bind(Self.node_env, Entity.env_elements)

    @langkit_property(return_type=Equation, dynamic_vars=[env, origin])
    def call_argument_equation(formal_type=T.BaseTypeDecl.entity,
                               call_is_primitive_of=T.BaseTypeDecl):
        """
        Generate the equation that binds the type_var of this expression
        given its expected type in the context of a subprogram call. Handles
        the case where that call is a primitive of the given
        call_is_primitive_of type.
        """
        return If(formal_type.accessed_type._or(formal_type).then(
            lambda at: at._.canonical_type.node.as_bare_entity.matching_type(
                call_is_primitive_of.as_bare_entity
            )),
            Bind(Entity.type_var, formal_type,
                 eq_prop=BaseTypeDecl.matching_formal_prim_type),
            Bind(Entity.type_var, formal_type,
                 eq_prop=BaseTypeDecl.matching_formal_type)
        )


class ContractCaseAssoc(BaseAssoc):
    """
    Single association for the ``Contract_Case`` aspect.
    """
    guard = Field(type=T.AdaNode)
    consequence = Field(type=T.Expr)

    assoc_expr = Property(Entity.consequence)


class ContractCases(Expr):
    """
    List of associations for the ``Contract_Case`` aspect.
    """
    contract_cases = Field(ContractCaseAssoc.list)


class ParenExpr(Expr):
    """
    Parenthesized expression.
    """
    expr = Field(type=T.Expr)

    @langkit_property()
    def xref_equation():
        return (
            Entity.expr.sub_equation
            & TypeBind(Self.expr.type_var, Self.type_var)
        )


class UnOp(Expr):
    """
    Unary expression.
    """

    op = Field(type=T.Op)
    expr = Field(type=T.Expr)

    @langkit_property()
    def xref_equation():
        subps = Var(Entity.op.subprograms.filter(
            lambda s: s.subp_spec_or_null.nb_max_params == 1
        ))
        return Entity.expr.sub_equation & (subps.logic_any(lambda subp: Let(
            lambda
            ps=subp.subp_spec_or_null.unpacked_formal_params,
            prim_type=subp.info.md.primitive.cast(T.BaseTypeDecl):

            # The subprogram's first argument must match Self's left
            # operand.
            Entity.expr.call_argument_equation(
                ps.at(0).spec.formal_type, prim_type
            )

            # The subprogram's return type is the type of Self
            & TypeBind(Self.type_var,
                       subp.subp_spec_or_null.return_type)

            # The operator references the subprogram
            & Bind(Self.op.ref_var, subp)
        )) | TypeBind(Self.type_var, Self.expr.type_var))


class BinOp(Expr):
    """
    Binary expression.
    """

    left = Field(type=T.Expr)
    op = Field(type=T.Op)
    right = Field(type=T.Expr)

    @langkit_property()
    def xref_equation():
        subps = Var(Entity.op.subprograms.filter(
            lambda s: s.subp_spec_or_null.nb_max_params == 2
        ))

        # When the operator is "/=" and there are no explicit overload, we
        # might refer to the implicit declaration of the "/=" operator that
        # comes with any overload of "=".
        refers_to_synthetic_neq = Var(And(
            subps.length == 0,
            Self.op.subprogram_symbol == '"/="'
        ))

        # So if that's the case, look for declarations of "="
        refined_subps = If(
            refers_to_synthetic_neq,
            Self.op.subprograms_for_symbol('"="', Entity).filter(
                lambda s: s.subp_spec_or_null.nb_max_params == 2
            ),
            subps
        )

        return (
            Entity.left.sub_equation
            & Entity.right.sub_equation
        ) & (refined_subps.logic_any(lambda subp: Let(
            lambda
            ps=subp.subp_spec_or_null.unpacked_formal_params,
            prim_type=subp.info.md.primitive.cast(T.BaseTypeDecl):

            # The subprogram's first argument must match Self's left
            # operand.
            Entity.left.call_argument_equation(
                ps.at(0).spec.formal_type, prim_type
            )

            # The subprogram's second argument must match Self's right
            # operand.
            & Entity.right.call_argument_equation(
                ps.at(1).spec.formal_type, prim_type
            )

            # The subprogram's return type is the type of Self
            & TypeBind(Self.type_var,
                       subp.subp_spec_or_null.return_type)

            # The operator references the subprogram, except when it refers
            # to the implicitly generated '/='.
            & Bind(
                Self.op.ref_var,
                If(refers_to_synthetic_neq, No(BasicDecl.entity), subp)
            )
        )) | Self.no_overload_equation)

    @langkit_property(dynamic_vars=[origin])
    def no_overload_equation():
        """
        When no subprogram is found for this node's operator, use this property
        to construct the xref equation for this node.
        """
        return Self.op.match(
            lambda _=Op.alt_pow:

            TypeBind(Self.right.type_var, Self.universal_int_type)
            & TypeBind(Self.left.type_var, Self.type_var),

            lambda _=Op.alt_concat: Or(
                TypeBind(Self.type_var, Self.left.type_var)
                & TypeBind(Self.type_var, Self.right.type_var),

                TypeBind(Self.type_var, Self.left.type_var)
                & TypeBind(Self.left.type_var, Self.right.type_var,
                           conv_prop=BaseTypeDecl.comp_type),

                TypeBind(Self.type_var, Self.right.type_var)
                & TypeBind(Self.right.type_var, Self.left.type_var,
                           conv_prop=BaseTypeDecl.comp_type),

                TypeBind(Self.right.type_var, Self.left.type_var)
                & TypeBind(Self.type_var, Self.right.type_var,
                           conv_prop=BaseTypeDecl.comp_type)

            ),

            # We treat .. differently from other binary operators, because in
            # the case of range of chars, as in 'a' .. 'z', type needs to flow
            # upward, from the operator to the operands.
            lambda _=Op.alt_double_dot: And(
                TypeBind(Self.type_var, Self.left.type_var),
                TypeBind(Self.type_var, Self.right.type_var)
            ),


            lambda _: Or(
                # Regular case: Both operands and binop are of the same type.
                # We call canonical_type as a conversion property because
                # operators are defined on the root subtype, so the return
                # value will always be of the root subtype.
                TypeBind(Self.left.type_var, Self.type_var,
                         conv_prop=BaseTypeDecl.canonical_type_or_null)
                & TypeBind(Self.right.type_var, Self.type_var,
                           conv_prop=BaseTypeDecl.canonical_type_or_null),

                # Universal real with universal int case: Implicit conversion
                # of the binop to universal real.
                # TODO: Apparently this is valid only for some operators,
                # and only in constant decls? Should clarify the legality
                # scope and only emit the following code when needed.
                Or(
                    universal_int_bind(Self.left.type_var)
                    & universal_real_bind(Self.right.type_var),

                    universal_real_bind(Self.left.type_var)
                    & universal_int_bind(Self.right.type_var)
                ) & universal_real_bind(Self.type_var)
            )
        )


class RelationOp(BinOp):
    """
    Binary operation that compares two value, producing a boolean.
    """
    no_overload_equation = Property(
        TypeBind(Self.left.type_var, Self.right.type_var)
        & bool_bind(Self.type_var)
    )


class MembershipExpr(Expr):
    """
    Represent a membership test (in/not in operators).

    Note that we don't consider them as binary operators since multiple
    expressions on the right hand side are allowed.
    """
    expr = Field(type=T.Expr)
    op = Field(type=T.Op)
    membership_exprs = Field(type=T.ExprAlternativesList)

    xref_equation = Property(
        bool_bind(Self.type_var)
        & Entity.expr.sub_equation
        &
        Entity.membership_exprs.logic_all(
            lambda m: m.cast(T.Name)._.name_designated_type.then(
                # Tagged type check
                lambda _: m.cast(T.Name).xref_no_overloading,

                # Regular membership check
                default_val=m.sub_equation & TypeBind(
                    Entity.expr.type_var, m.type_var
                )
            )
        ),
    )


class MultidimAggregateInfo(Struct):
    """
    Struct enclosing information about aggregates for multidimensional array
    types.
    """
    agg = UserField(type=T.BaseAggregate.entity, doc="the top level aggregate")
    typ = UserField(type=T.BaseTypeDecl.entity, doc="the type of the array")
    rank = UserField(type=Int, doc="the rank of the original sub-aggregate")


@abstract
class BaseAggregate(Expr):
    """
    Base class for aggregates.
    """

    ancestor_expr = Field(type=T.Expr)
    assocs = Field(type=T.AssocList)

    xref_stop_resolution = Property(True)

    # An aggregate is resolved separately from the rest of an expression,
    # however, resolution of the containing expression can leverage the
    # knowledge that self is an aggregate, by accepting only type that can be
    # represented by an aggregate (eg. records and arrays).
    stop_resolution_equation = Property(bind_origin(
        Self, Predicate(BaseTypeDecl.is_array_or_rec, Self.type_var)
    ))

    @langkit_property(return_type=MultidimAggregateInfo, dynamic_vars=[origin])
    def multidim_root_aggregate(r=(Int, 0)):
        """
        Return the root parent aggregate if Self is part of a multidimensional
        array aggregate (either the root or a sub-aggregate).
        """
        # Nested aggregates of a multidimensional array have no types, so we're
        # searching for the first aggregate with a type inside type_val.
        return Entity.type_val.cast(BaseTypeDecl).then(
            lambda tv: If(
                # If we have a multidimensional array type here, return all the
                # needed info (rank, root aggregate and type of the array).
                tv.array_ndims > 1,
                MultidimAggregateInfo.new(agg=Entity, typ=tv, rank=r),

                # If we're here, we found a type, and it's not a multidim
                # array: Stop there.
                No(T.MultidimAggregateInfo)
            ),
            # If we're here, there is a parent aggregate and no type_val:
            # recurse up.
            default_val=Entity.parent.parent.parent.cast(T.Aggregate)
            ._.multidim_root_aggregate(r + 1)
        )

    @langkit_property()
    def xref_equation():
        return If(
            Self.parent.is_a(AspectClause, AspectAssoc, PragmaArgumentAssoc),
            LogicTrue(),
            Entity.general_xref_equation
        )

    @langkit_property(return_type=Equation, dynamic_vars=[env, origin])
    def general_xref_equation():

        # Self might be part of a multidim array aggregate. In that case, get
        # the root parent aggregate.
        mra = Var(Entity.multidim_root_aggregate)

        # If we're part of a multidim aggregate, then take the root aggregate's
        # type. Else, this is a regular aggregate. In this case grab the type
        # in type_val.
        td = Var(If(mra.is_null, Self.type_val.cast(BaseTypeDecl), mra.typ))

        atd = Var(td.array_def)

        return Cond(
            atd.is_null,

            # First case, aggregate for a record
            Entity.ancestor_expr.then(
                lambda ae: ae.sub_equation, default_val=LogicTrue()
            )
            & Entity.record_equation(td),

            # Second case, aggregate for an array
            Entity.assocs.logic_all(
                lambda assoc:
                If(
                    # If the array is monodimensional, or we're on the last
                    # dimension of a multidimensional array ..
                    Or(mra.is_null, mra.rank == td.array_ndims - 1),

                    # .. Then we want to match the component type
                    assoc.expr.sub_equation
                    & TypeBind(assoc.expr.type_var, atd.comp_type),

                    # .. Else we're on an intermediate dimension of a
                    # multidimensional array: do nothing.
                    LogicTrue()
                )

                & assoc.names.logic_all(
                    lambda n:
                    n.as_entity.sub_equation
                    & n.cast(T.Expr).then(
                        lambda n: TypeBind(n.type_var,
                                           atd.index_type(mra.rank)),
                        default_val=LogicTrue()
                    )
                )
            )
        )

    @langkit_property(return_type=Equation, dynamic_vars=[env, origin])
    def record_equation(td=BaseTypeDecl.entity):
        """
        Equation for the case where this is an aggregate for a record
        type.
        """

        all_params = (
            td.record_def
            .comps.abstract_formal_params_for_assocs(Entity.assocs)
        )

        # Match formals to actuals, and compute equations
        return Self.match_formals(all_params, Entity.assocs, False).logic_all(
            lambda pm:
            TypeBind(pm.actual.assoc.expr.type_var,
                     pm.formal.spec.type_expression.designated_type)
            & pm.actual.assoc.expr.sub_equation
            & pm.actual.name.then(lambda n: Bind(n.ref_var, pm.formal.spec),
                                  LogicTrue())
        )


class Aggregate(BaseAggregate):
    """
    Aggregate that is not a ``null record`` aggregate.
    """


class NullRecordAggregate(BaseAggregate):
    """
    Aggregate for ``null record``.
    """


class ExpectedTypeForExpr(Struct):
    """
    Struct used by ``potential_actuals_for_dispatch`` to store an expression
    together with the type that is expected for it.
    """
    expected_type = UserField(type=T.TypeExpr.entity)
    expr = UserField(type=T.Expr.entity)


@abstract
class Name(Expr):
    """
    Base class for names.
    """

    enclosing_defining_name = Property(
        Entity.parents.find(lambda p: p.is_a(T.DefiningName))
        .cast(T.DefiningName),
        public=True, doc="""
        If this name is part of a defining name, return the enclosing defining
        name node.
        """,
    )

    @langkit_property(public=True, return_type=T.Bool)
    def is_defining():
        """
        Return True if this name is part of a defining name.
        """
        return Not(Or(
            Entity.enclosing_defining_name.is_null,
            Self.parent.cast(T.DottedName).then(
                lambda dn: dn.suffix != Self
            )
        ))

    parent_scope = AbstractProperty(
        type=LexicalEnv, runtime_check=True,
        dynamic_vars=[env],
        doc="""
        Returns the lexical environment that is the scope in which the
        entity designated by this name is defined/used.
        """
    )

    @langkit_property(public=True, return_type=T.Bool)
    def name_is(sym=(T.Symbol)):
        """
        Helper. Check that this name matches ``sym``.
        """
        return Self.name_symbol.then(lambda ns: ns == sym)

    @langkit_property(public=True, return_type=T.Bool)
    def is_direct_call():
        """
        Return True iff this name represents a call to a subprogram which is
        referred by its defining name. (i.e. not through a subprogram access).
        """
        return And(
            Entity.is_call,
            Not(Entity.called_subp_spec.parent.is_a(AccessToSubpDef))
        )

    @langkit_property(public=True, return_type=T.Bool)
    def is_access_call():
        """
        Return True iff this name represents a call to subprogram through
        an access type.
        """
        return And(
            Entity.is_call,
            Entity.called_subp_spec.parent.is_a(AccessToSubpDef)
        )

    @langkit_property(public=True, return_type=T.Bool)
    def is_call():
        """
        Returns True if this Name corresponds to a call.
        """
        return And(
            Not(Entity.is_defining),
            Not(Entity.called_subp_spec.is_null)
        )

    @langkit_property(public=True, return_type=T.Bool,
                      dynamic_vars=[default_imprecise_fallback()])
    def is_dot_call():
        """
        Returns True if this Name corresponds to a dot notation call.
        """
        return And(
            Not(Entity.is_defining),
            Entity.referenced_decl.info.md.dottable_subp
        )

    @langkit_property(public=True, return_type=T.DefiningName.entity)
    def referenced_id(ref_decl=T.BasicDecl.entity):
        """
        Like ``referenced_decl``, but will return the defining identifier for
        the decl, rather than the basic declaration node itself.
        """
        return ref_decl.then(lambda ref_decl: Entity.name_symbol.then(
            lambda rel_name: ref_decl.defining_names.find(
                lambda dn: dn.name_is(rel_name)
            )
        )._or(ref_decl.defining_name), default_val=No(T.DefiningName.entity))

    @langkit_property(public=True, return_type=T.DefiningName.entity)
    def xref():
        dn = Var(Entity.is_defining.then(
            lambda _: Entity.enclosing_defining_name
        ))
        bd = Var(dn.then(lambda dn: dn.basic_decl))

        return Cond(
            bd.then(lambda bd: bd.is_a(T.ParamSpec)),
            bd.cast(T.ParamSpec).decl_param(dn),

            bd.then(lambda bd: bd.is_a(Body)),
            bd.cast(T.Body).decl_part._.defining_name,

            bd.then(lambda bd: bd.is_a(BaseTypeDecl)
                    & bd.cast(T.BaseTypeDecl).is_in_private_part),
            bd.cast(T.BaseTypeDecl).previous_part(True)._.defining_name,

            bd.then(lambda bd: bd.is_a(ObjectDecl)),
            # TODO: Implement jumping to full object decl view for constant
            # object decls with no value.
            No(T.DefiningName.entity),

            bd.then(lambda bd: bd.is_a(NumberDecl)),
            # Number decls cannot have a next part, always return None
            No(T.DefiningName.entity),

            bd.then(lambda bd: bd.is_a(BasicDecl)),
            bd.body_part_for_decl.then(lambda bpe: bpe.defining_name)
            ._or(bd.defining_name),

            Entity.referenced_id(Entity.referenced_decl)
        )

    @langkit_property(return_type=AdaNode.entity.array,
                      kind=AbstractKind.abstract_runtime_check,
                      dynamic_vars=[env, origin])
    def all_env_els_impl(seq=(Bool, True),
                         seq_from=(AdaNode, No(T.AdaNode))):
        pass

    @langkit_property(public=True)
    def all_env_elements(seq=(Bool, True),
                         seq_from=(AdaNode, No(T.AdaNode))):
        """
        Return all elements in self's scope that are lexically named like Self.
        """
        return bind_origin(
            Self, env.bind(Entity.node_env,
                           Entity.all_env_els_impl(seq=seq, seq_from=seq_from))
        )

    @langkit_property(public=True)
    def first_corresponding_decl():
        return Entity.all_env_elements().at(0).cast(T.BasicDecl)

    @langkit_property(return_type=Equation, dynamic_vars=[env, origin])
    def bottom_up_name_equation():
        return Self.innermost_name.as_entity.match(
            lambda ce=T.CallExpr: ce.general_xref_equation(Self),
            lambda ed=T.ExplicitDeref: ed.general_xref_equation(Self),
            lambda qe=T.QualExpr: qe.general_xref_equation(Self),
            lambda _: LogicFalse(),
        )

    @langkit_property(return_type=T.Name, ignore_warn_on_node=True)
    def innermost_name():
        """
        Helper property. Return the innermost name following the name chain.
        For example, given::

            A (B) (C) (D)
            ^-----------^ Self
            ^-------^     Self.name
            ^---^         Self.name.name

        `Self.innermost_name` will return the node corresponding to
        `Self.name.name`.
        """
        name = Var(Self.match(
            lambda ce=T.CallExpr: ce.name,
            lambda ed=T.ExplicitDeref: ed.prefix,
            lambda _: No(T.Name)

        ))

        return Cond(
            name.is_a(T.CallExpr, T.ExplicitDeref), name.innermost_name,
            name.is_a(T.QualExpr), name,
            Self
        )

    @langkit_property(return_type=Equation, dynamic_vars=[env, origin])
    def parent_name_equation(typ=T.BaseTypeDecl.entity, root=T.Name):
        """
        Construct the xref equation for the chain of parent nested names.
        """
        as_subp_access = Var(typ._.access_def.cast(AccessToSubpDef))
        is_paramless = Var(as_subp_access._.subp_spec.paramless(
            dottable_subp=False,
            can_be=False
        ))
        can_be_paramless = Var(as_subp_access._.subp_spec.paramless(
            dottable_subp=False,
            can_be=True
        ))
        comp_type = Var(
            typ._.comp_type(is_subscript=Not(Self.is_a(ExplicitDeref)))
        )
        return If(
            typ.is_null,
            LogicFalse(),

            Self.match(
                lambda ce=T.CallExpr:
                ce.as_entity.subscriptable_type_equation(typ),

                lambda ed=T.ExplicitDeref:
                ed.as_entity.eq_for_type(typ)

                # If ``typ`` is an access to subprogram, it means Self (an
                # ExplicitDeref) is actually a call to that subprogram. So,
                # bind its subp_spec_var to the subprogram spec of the access.
                & Bind(Self.subp_spec_var, as_subp_access._.subp_spec),

                lambda _: Bind(Self.type_var, No(T.AdaNode.entity).node),
            ) & Self.parent_name(root).as_entity.then(
                lambda pn: If(
                    Self.is_a(T.ExplicitDeref) & Not(as_subp_access.is_null),

                    # If Self is an explicit deref of a subprogram access type,
                    # we need to handle several cases:
                    Cond(
                        # The subprogram doesn't take parameters, in which case
                        # the explicit dereference necessarily means accessing
                        # the component type of the access type (it represents
                        # the call).
                        is_paramless,
                        pn.parent_name_equation(comp_type, root),

                        # The subprogram can be called without parameters, in
                        # which case we don't know for sure whether the
                        # explicit dereference accesses the component type or
                        # if it is the parent CallExpr that will.
                        can_be_paramless,
                        Or(
                            pn.parent_name_equation(comp_type, root),
                            pn.parent_name_equation(typ, root),
                        ),

                        # The subprogram must be called with parameters, in
                        # which case the parent CallExpr will expect the non-
                        # dereferenced type.
                        pn.parent_name_equation(typ, root)
                    ),

                    # Otherwise the explicit deref necessarily accesses the
                    # component type of typ.
                    pn.parent_name_equation(comp_type, root)
                ),
                default_val=LogicTrue()
            )
        )

    @langkit_property(return_type=Equation, dynamic_vars=[env, origin])
    def subtype_indication_equation():
        return Entity.xref_no_overloading

    @langkit_property(return_type=T.Name, ignore_warn_on_node=True)
    def parent_name(stop_at=T.Name):
        """
        Will return the parent name until the stop point.
        """
        return If(stop_at.is_null | (Self == stop_at),
                  No(T.Name),
                  Self.parent.cast(T.Name))

    @langkit_property(return_type=T.CallExpr.entity)
    def parent_callexpr():
        """
        If this name qualifies the prefix in a call expression, this returns
        the corresponding CallExpr node. Return null otherwise. For example::

            C (12, 15);
            ^ parent_callexpr = <CallExpr>

            A.B.C (12, 15);
                ^ parent_callexpr = <CallExpr>

            A.B.C (12, 15);
              ^ parent_callexpr = null

            C (12, 15);
               ^ parent_callexpr = null
        """
        return Entity.parents.take_while(lambda p: Or(
            p.is_a(CallExpr),
            p.is_a(DottedName, BaseId) & p.parent.match(
                lambda pfx=DottedName: pfx.suffix == p,
                lambda ce=CallExpr: ce.name == p,
                lambda _: False
            )
        )).find(lambda p: p.is_a(CallExpr)).cast(CallExpr)

    @langkit_property(return_type=Bool)
    def is_range_attribute():
        """
        Predicate that returns True if self is a range attribute ref.
        """
        return Self.cast(T.AttributeRef).then(
            lambda attr_ref:
            attr_ref.as_bare_entity.attribute.name_is('Range')
        )

    scope = Property(
        EmptyEnv, dynamic_vars=[env],
        doc="""
        Lexical environment this identifier represents. This is similar to
        designated_env although it handles only cases for child units and it is
        used only during the environment population pass so it does not return
        orphan environments.
        """
    )

    @langkit_property(return_type=T.Bool)
    def is_simple_name():
        """
        Returns whether Self is a BaseId or a DottedName composed only of
        BaseIds.
        """
        return Self.match(
            lambda _=T.BaseId: True,
            lambda dt=T.DottedName: dt.prefix.is_simple_name,
            lambda _: False
        )

    @langkit_property(kind=AbstractKind.abstract_runtime_check,
                      return_type=LogicVar)
    def ref_var():
        """
        This property proxies the logic variable that points to the entity that
        this name refers to. For example, for a simple dotted name::

            A.B

        The dotted name's ref var is the one of the SingleTokNode B.
        """
        pass

    @langkit_property(kind=AbstractKind.abstract_runtime_check,
                      return_type=LogicVar)
    def subp_spec_var():
        """
        This logic variable holds the specification of the subprogram or
        subprogram access that is being called by this exact Name.
        """
        pass

    @langkit_property(return_type=T.Bool)
    def defines_subp_spec_var():
        # A null logic variable could have been used instead of this additional
        # property to indicate that an AST node does not define subp_spec_var.
        # Unfortunately, No(LogicVar) is not a valid dsl expression. Therefore,
        # we provide a default implementation for this property, which is then
        # overriden in relevant child classes to indicate that one can call
        # p_subp_spec_var.
        return False

    @langkit_property(public=True, return_type=T.BaseFormalParamHolder.entity)
    def called_subp_spec():
        """
        Return the subprogram specification of the subprogram or subprogram
        access that is being called by this exact Name, if relevant.
        """
        return If(
            Self.defines_subp_spec_var,
            Let(
                lambda _=Entity.resolve_names_from_closest_entry_point:
                Self.subp_spec_var.get_value
                .cast_or_raise(BaseFormalParamHolder)
            ),
            No(BaseFormalParamHolder.entity)
        )

    @langkit_property(public=True,
                      dynamic_vars=[default_imprecise_fallback()])
    def referenced_decl():
        """
        Return the declaration this node references after name resolution.
        If imprecise_fallback is True, errors raised during resolution of the
        xref equation are catched and a fallback mechanism is triggered, which
        tries to find the referenced declaration in an ad-hoc way.
        """
        return Entity.referenced_decl_internal(False)

    @langkit_property(public=True,
                      dynamic_vars=[default_imprecise_fallback()])
    def referenced_decl_internal(try_immediate=Bool):
        """
        Return the declaration this node references. Try not to run name res if
        already resolved. INTERNAL USE ONLY.
        """
        return If(
            Entity.is_defining,
            PropertyError(
                T.BasicDecl.entity,
                "Cannot call referenced_decl on a defining name"
            ),
            Entity.referenced_decl_internal_helper(Self.ref_var, try_immediate)
        )

    designated_type_impl = Property(
        No(BaseTypeDecl.entity),
        dynamic_vars=[env, origin],
        doc="""
        Assuming this name designates a type, return this type.

        Since in Ada this can be resolved locally without any non-local
        analysis, this doesn't use logic equations.
        """
    )

    name_designated_type = Property(
        env.bind(Entity.node_env,
                 bind_origin(Self, Entity.designated_type_impl)),
        doc="""
        Like SubtypeIndication.designated_type, but on names, since because of
        Ada's ambiguous grammar, some subtype indications will be parsed as
        names.
        """,
        public=True
    )

    @langkit_property(return_type=Bool,
                      dynamic_vars=[default_imprecise_fallback()],
                      public=True)
    def is_static_subtype():
        """
        Returns whether Self denotes a static subtype or not.
        """
        return Entity.name_designated_type.is_static_decl

    @langkit_property(memoized=True)
    def name_designated_type_env():
        return Entity.name_designated_type._.primitives_env

    @langkit_property(
        return_type=AnalysisUnit, external=True, uses_entity_info=False,
        uses_envs=False,
        call_non_memoizable_because='Getting an analysis unit cannot appear'
                                    ' in a memoized context'
    )
    def internal_referenced_unit(kind=AnalysisUnitKind,
                                 load_if_needed=Bool):
        """
        Return the analysis unit for the given ``kind`` corresponding to this
        Name. Return null if this is an illegal unit name. If
        ``load_if_needed`` is false and the target analysis unit is not loaded
        yet, don't load it and return a null unit.
        """
        pass

    @langkit_property()
    def referenced_unit(kind=AnalysisUnitKind):
        """
        Shortcut for: `.internal_referenced_unit(kind, True)`.
        """
        return Self.internal_referenced_unit(kind, True)

    @langkit_property(call_memoizable=True)
    def referenced_unit_or_null(kind=AnalysisUnitKind):
        """
        Shortcut for: `.internal_referenced_unit(kind, False)`.
        """
        return Self.internal_referenced_unit(kind, False)

    @langkit_property(return_type=Bool)
    def matches(n=T.Name):
        """
        Return whether two names match each other.

        This compares the symbol for Identifier and StringLiteral nodes. We
        consider that there is no match for all other node kinds.
        """
        return Self.match(
            lambda id=Identifier:
                n.cast(Identifier).then(
                    lambda other_id: id.sym.equals(other_id.sym)
                ),
            lambda sl=StringLiteral:
                n.cast(StringLiteral).then(
                    lambda other_sl: sl.sym.equals(other_sl.sym)
                ),
            lambda di=DefiningName: n.matches(di.name),
            lambda _: False
        )

    @langkit_property(public=True)
    def name_matches(n=T.Name.entity):
        """
        Return whether two names match each other.

        This compares the symbol for Identifier and StringLiteral nodes. We
        consider that there is no match for all other node kinds.
        """
        return Self.matches(n.node)

    @langkit_property()
    def use_package_name_designated_env():
        """
        Assuming Self is a name that is the direct child of a
        UsePackageClause's package name list, return the memoized designated
        environment for it.
        """
        return (Self.parent.parent.cast_or_raise(T.UsePackageClause)
                .designated_envs.at(Self.child_index))

    relative_name = AbstractProperty(
        type=T.SingleTokNode.entity, runtime_check=True, public=True,
        doc="""
        Returns the relative name of this instance. For example,
        for a prefix A.B.C, this will return C.
        """
    )

    name_symbol = Property(Self.as_bare_entity.relative_name.symbol)

    base_name = Property(
        No(T.Name.entity),
        doc="""
        Returns the base name of this instance. For example,
        for a prefix A.B.C, this will return A.B.
        """
    )

    @langkit_property(return_type=Equation, dynamic_vars=[env, origin])
    def xref_no_overloading(sequential=(Bool, True),
                            all_els=(Bool, False)):
        """
        Simple xref equation for names. Doesn't try to resolve overloads. If
        ``all_els`` is True, then the name will be bound to the domain of all
        elements that corresponds. Else, it will be bound to the first one.

        ``sequential`` determines whether the lookup will be sequential or not.
        """
        return Entity.match(

            lambda dn=T.DottedName:
            dn.prefix.xref_no_overloading(sequential, all_els)
            & env.bind(
                dn.prefix.designated_env,
                dn.suffix.xref_no_overloading(sequential, all_els)
            ),
            lambda i=T.BaseId: If(
                all_els & Self.is_suffix,
                i.ref_var.domain(
                    env_get(
                        env,
                        i.name_symbol,
                        from_node=If(sequential, Entity.node, No(T.Name)),
                        lookup=If(Self.is_prefix, LK.recursive, LK.flat),
                    ),
                ),
                Bind(
                    i.ref_var,
                    env_get_first(
                        env,
                        i.name_symbol,
                        from_node=If(sequential, Entity.node, No(T.Name)),
                        lookup=If(Self.is_prefix, LK.recursive, LK.flat),
                        categories=noprims,
                    ),
                )
            ),

            # xref_no_overloading can be used to resolve type references in
            # generic instantiations. In that case, we might encounter a 'Class
            # attribute.
            lambda ar=T.AttributeRef:
            ar.prefix.xref_no_overloading(sequential, all_els)
            & If(ar.attribute.sym == 'Class',
                 Bind(ar.prefix.ref_var, ar.ref_var,
                      conv_prop=BaseTypeDecl.classwide_type),
                 LogicTrue()),


            lambda _: LogicFalse()
        )

    @langkit_property(return_type=T.Bool, memoized=True)
    def is_prefix():
        """
        Returns whether Self is the prefix in name. Is used to determine
        whether lookups on this name should be recursive or not, without having
        to pass down the information as a function parameter.
        """
        return Or(
            Self.parent.is_a(T.DottedName)
            & (Self.parent.cast(T.DottedName).prefix == Self)
            & Self.parent.cast(T.Name).is_prefix,
            Not(Self.parent.is_a(T.DottedName))
        )

    @langkit_property(return_type=T.Bool, memoized=True)
    def is_suffix():
        """
        Returns whether Self is the suffix in name.
        """
        return Or(
            Self.parent.is_a(T.DottedName)
            & (Self.parent.cast(T.DottedName).suffix == Self)
            & Self.parent.cast(T.Name).is_suffix,

            Not(Self.parent.is_a(T.DottedName))
        )

    is_operator_name = Property(
        Entity.name_symbol.any_of(
            '"="',  '"="', '"/="', '"<"', '"<="', '">"', '">="', '"and"',
            '"or"', '"xor"', '"abs"', '"*"', '"/"', '"mod"', '"rem"', '"+"',
            '"-"', '"&"' '"+"', '"-"', '"not"', '"abs"'
        )
    )

    @langkit_property(public=True, return_type=T.Bool,
                      dynamic_vars=[default_imprecise_fallback()])
    def is_write_reference():
        """
        Whether this name is a write reference.

        For example, `X` is a write reference in the following cases::

          1. `X := 2;`
          2. `X (2) := 2;`
          3. `P(F => X)` where F is declared `out` or `in out`.
          4. `X'Access`.
          5. `X.C := 2`, `R.X := 2`

        .. note:: This is an experimental feature. There might be some
            discrepancy with the GNAT concept of "write reference".
        """
        return Entity.parent.match(
            # Handle assignment case::
            #     X := 2;
            lambda a=T.AssignStmt: a.dest == Entity,

            # Handle assignment to component case::
            #     X (2) := 2;
            lambda c=T.CallExpr: And(
                c.name == Entity,  # Self is the name of component access
                c.is_write_reference
            ),
            # Handle assignment to component case::
            #    X.C := 2
            #    R.X := 2
            lambda d=T.DottedName: d.is_write_reference,

            # Handle out/inout param case
            lambda p=T.ParamAssoc: p.get_params.any(
                lambda m: m.basic_decl.cast(T.ParamSpec)._.mode.is_a(
                    Mode.alt_out, Mode.alt_in_out
                )
            ),

            # handle 'Access case
            lambda a=T.AttributeRef: (a.prefix == Entity) & a.is_access_attr,

            lambda _: False
        )

    @langkit_property(return_type=ExpectedTypeForExpr.array,
                      dynamic_vars=[default_imprecise_fallback()])
    def potential_actuals_for_dispatch():
        """
        Assuming Self is a call to a subprogram, return an array of pairs
        (expected_type, expression) for each expression in and around the call
        that could be used for performing a dynamic dispatch for this call.
        """
        spec = Var(Entity.referenced_decl.subp_spec_or_null)

        # Handle the case where the dispatch is done on the tag of the
        # LHS expr of an assign statement. In that case, its expected type
        # is the return type of the called subprogram.
        ret = Var(
            spec.returns.then(lambda rt: Entity.parent.cast(T.AssignStmt).then(
                lambda a: ExpectedTypeForExpr.new(
                    expected_type=rt,
                    expr=a.dest
                ).singleton
            ))
        )

        # Handle calls done using the dot notation. Retrieve the prefix and
        # match it with the type of the first parameter of the called
        # subprogram.
        prefix = Var(
            Entity.is_dot_call.then(lambda _: Entity.match(
                lambda c=T.CallExpr: c.name.cast_or_raise(T.DottedName),
                lambda d=T.DottedName: d,
                lambda i=T.Identifier: i.parent.cast_or_raise(T.DottedName),
                lambda _: No(T.DottedName.entity)
            ).then(lambda d: ExpectedTypeForExpr.new(
                expected_type=spec.unpacked_formal_params.at(0)
                .spec.type_expression,
                expr=d.prefix
            ).singleton))
        )

        # Handle the rest of the arguments if this is a CallExpr, matching
        # them with the types of the parameters of the called subprogram.
        args = Var(
            Entity.cast(T.CallExpr)._.params._.zip_with_params.map(
                lambda pm: ExpectedTypeForExpr.new(
                    expected_type=pm.param.basic_decl.type_expression,
                    expr=pm.actual
                )
            )
        )

        return ret.concat(prefix).concat(args)

    @langkit_property(public=True, return_type=T.Bool,
                      dynamic_vars=[default_imprecise_fallback()])
    def is_dispatching_call():
        """
        Returns True if this Name corresponds to a dispatching call, including:
         - calls done through subprogram access types.
         - calls to dispatching subprograms, in the object-oriented sense.

        .. note:: This is an experimental feature. There might be some
            discrepancy with the GNAT concept of "dispatching call".
        """
        return Or(Entity.is_access_call, Entity.is_direct_call & Let(
            lambda
            decl=Entity.referenced_decl,

            # Retrieve the candidate expressions on which the tag check could
            # be made, together with the expected type for them.
            candidates=Entity.parent_callexpr.cast(T.Name.entity)
            ._or(Entity).then(lambda e: e.potential_actuals_for_dispatch):

            Let(
                # For all candidate pair (expected_type, expression), check
                # that the expected type can indeed designate a type of
                # which the called subprogram is a primitive, and that
                # the corresponding expression is its classwide type.
                lambda s=decl.canonical_part.subp_spec_or_null: candidates.any(
                    lambda c: And(
                        Not(s.candidate_type_for_primitive(c.expected_type)
                            .is_null),
                        # No need to check that the type of the expression
                        # is exactly the classwide type of the expected
                        # type, but simply that it is classwide.
                        c.expr.expression_type.is_classwide
                    )
                )
            )
        ))

    @langkit_property(public=True, return_type=T.Bool,
                      dynamic_vars=[default_imprecise_fallback()])
    def is_static_call():
        """
        Returns True if this Name corresponds to a static non-dispatching call.
        In other words, this will return True if and only if the target of the
        call is known statically.

        .. note:: This is an experimental feature. There might be some
            discrepancy with the GNAT concept of "static call".
        """
        return Entity.is_call & Not(Entity.is_dispatching_call)

    @langkit_property(public=True, return_type=Symbol.array)
    def as_symbol_array():
        """
        Turn this name into an array of symbols.

        For instance, a node with name ``A.B.C`` is turned into
        ``['A', 'B', 'C']``.
        """
        return Self.match(
            lambda dname=T.DefiningName: dname.name.as_symbol_array,
            lambda tok=T.SingleTokNode: tok.symbol.singleton,
            lambda dot=T.DottedName: dot.prefix.as_symbol_array.concat(
                dot.suffix.as_symbol_array
            ),
            lambda _: PropertyError(Symbol.array),
        )


class DiscreteSubtypeName(Name):
    """
    Subtype name for membership test expressions.
    """

    subtype = Field(type=T.DiscreteSubtypeIndication)


class TargetName(Name):
    """
    Name for Ada 2020's ``@``.
    """

    pass


class CallExpr(Name):
    """
    Represent a syntactic call expression.

    At the semantic level, this can be either a subprogram call, an array
    subcomponent access expression, an array slice or a type conversion.
    """
    name = Field(type=T.Name)
    suffix = Field(type=T.AdaNode)

    ref_var = Property(Self.name.ref_var)

    r_called_spec = UserField(LogicVar, public=False)

    subp_spec_var = Property(Self.r_called_spec)
    defines_subp_spec_var = Property(True)

    relative_name = Property(Entity.name.relative_name)

    @langkit_property()
    def designated_env():
        typ = Var(Entity.name.name_designated_type)

        return If(
            Not(typ.is_null),
            typ.defining_env,
            Entity.env_elements.map(lambda e: e.match(
                lambda bd=BasicDecl.entity:       bd.defining_env,
                lambda _:                         EmptyEnv,
            )).env_group()
        )

    @langkit_property()
    def env_elements_impl():
        return Entity.name.env_elements_impl

    # CallExpr can appear in type expressions: they are used to create implicit
    # subtypes for discriminated records or arrays.
    designated_type_impl = Property(Entity.name.designated_type_impl)

    params = Property(Entity.suffix.cast(T.AssocList))

    @langkit_property(return_type=Equation)
    def xref_equation():
        return Entity.bottom_up_name_equation

    @langkit_property(return_type=Equation, dynamic_vars=[env, origin])
    def type_conv_xref_equation():
        """
        Helper for xref_equation, handles construction of the equation in type
        conversion cases.
        """
        return And(
            Entity.params.at(0).expr.sub_equation,
            Entity.name.subtype_indication_equation,
            Bind(Self.name.ref_var, Self.name.type_var),
            TypeBind(Self.type_var, Self.name.ref_var),
        )

    @langkit_property(return_type=Equation, dynamic_vars=[env, origin])
    def entity_equation(s=T.BasicDecl.entity, root=T.Name):
        # The called entity is the matched entity
        return Bind(Self.name.ref_var, s) & Cond(

            # If s does not have any parameters, then we construct the
            # chain of name equations starting from self, with the parent
            # component.
            s.is_paramless, Entity.parent_name_equation(
                s.expr_type, root
            ),

            # If S can be called in a paramless fashion, but can also be
            # called with parameters, we are forced to make a disjunction.
            s.can_be_paramless, Or(
                Entity.parent_name_equation(
                    s.expr_type, root
                ),

                And(
                    Entity.subprogram_equation(
                        s.subp_spec_or_null,
                        s.info.md.dottable_subp,
                        s.info.md.primitive
                    ),
                    Entity.parent_name(root).as_entity.then(
                        lambda pn:
                        pn.parent_name_equation(s.expr_type, root),
                        default_val=LogicTrue()
                    )
                )
            ),

            And(
                Entity.subprogram_equation(
                    s.subp_spec_or_null,
                    s.info.md.dottable_subp,
                    s.info.md.primitive
                ),
                Entity.parent_name(root).as_entity.then(
                    lambda pn:
                    pn.parent_name_equation(s.expr_type, root),
                    default_val=LogicTrue()
                )
            )
        )

    @langkit_property(return_type=Equation, dynamic_vars=[env, origin])
    def general_xref_equation(root=T.Name):
        """
        Helper for xref_equation, handles construction of the equation in
        subprogram call cases.
        """
        return If(
            Not(Entity.name.is_a(QualExpr))
            & Not(Entity.name.name_designated_type.is_null),

            # Type conversion case
            Entity.type_conv_xref_equation
            & Entity.parent_name(root).as_entity.then(
                lambda pn:
                pn.parent_name_equation(
                    Entity.name.name_designated_type, root),
                default_val=LogicTrue()
            ),

            And(
                Entity.params.logic_all(lambda pa: pa.expr.sub_equation),

                # For each potential entity match, we want to express the
                # following constraints:
                Let(lambda subps=Entity.env_elements: And(
                    subps.logic_any(lambda e: Let(
                        lambda s=e.cast_or_raise(BasicDecl.entity):
                        If(
                            s.cast(EntryDecl)._.spec.family_type.is_null,
                            Entity.entity_equation(s, root),
                            Self.parent_name(root).cast_or_raise(T.CallExpr)
                            .as_entity.entity_equation(s, root),
                        ),
                    )),
                    Bind(Self.ref_var, Self.name.ref_var),
                    Entity.name.sub_equation
                )) | If(Entity.name.is_simple_name,
                        Entity.operator_equation,
                        LogicFalse())
            )
        )

    @langkit_property(return_type=Equation, dynamic_vars=[env, origin])
    def operator_equation():
        """
        Equation for built-in operators.
        """
        rel_name = Var(Entity.name.name_symbol)

        def base_name_eq():
            return Entity.name.base_name.then(lambda n: n.sub_equation,
                                              default_val=LogicFalse())

        return Entity.params._.unpacked_params.then(
            lambda params:
            Cond(
                (params.length == 2)
                & rel_name.any_of('"="',  '"="', '"/="', '"<"', '"<="', '">"',
                                  '">="'),
                TypeBind(params.at(0).assoc.expr.type_var,
                         params.at(1).assoc.expr.type_var)
                & bool_bind(Self.type_var)
                & base_name_eq(),


                (params.length == 2)
                & rel_name.any_of(
                    '"and"', '"or"', '"xor"', '"abs"', '"*"',
                    '"/"', '"mod"', '"rem"', '"+"', '"-"', '"&"'
                ),
                TypeBind(params.at(0).assoc.expr.type_var,
                         params.at(1).assoc.expr.type_var)
                & TypeBind(params.at(0).assoc.expr.type_var,
                           Self.type_var)
                & base_name_eq(),

                (params.length == 2) & (rel_name == '"**"'),
                TypeBind(params.at(1).assoc.expr.type_var,
                         Self.universal_int_type)
                & TypeBind(params.at(0).assoc.expr.type_var, Self.type_var)
                & base_name_eq(),

                (params.length == 1)
                & rel_name.any_of('"+"', '"-"', '"not"', '"abs"'),
                TypeBind(params.at(0).assoc.expr.type_var, Self.type_var)
                & base_name_eq(),

                LogicFalse()
            ),
            default_val=LogicFalse()
        )

    @langkit_property(return_type=Equation, dynamic_vars=[env, origin])
    def subscriptable_type_equation(typ=T.BaseTypeDecl.entity,
                                    constrain_params=(Bool, True)):
        """
        Construct an equation verifying if Self is conformant to the type
        designator passed in parameter.
        """
        atd = Var(typ.then(lambda t: t.array_def_with_deref))
        real_typ = Var(typ.then(
            lambda t: If(t.is_implicit_deref, t.accessed_type, t))
        )

        return Cond(
            # First handle the case where this is an access to subprogram
            typ.access_def.is_a(AccessToSubpDef),
            typ.access_def.cast(AccessToSubpDef).then(
                lambda asd:
                Entity.subprogram_equation(asd.subp_spec, False, No(AdaNode))
                & Entity.params.logic_all(
                    lambda pa: pa.expr.sub_equation
                ),
                default_val=LogicFalse(),
            ),

            Not(atd._.indices.is_null), Entity.suffix.match(
                lambda _=T.AssocList: Or(
                    # Either a regular array access
                    Entity.params._.logic_all(
                        lambda i, pa: If(
                            constrain_params,
                            pa.expr.sub_equation,
                            LogicTrue()
                        )
                        & atd.indices.constrain_index_expr(pa.expr, i)
                    )
                    & TypeBind(Self.type_var, atd.comp_type),

                    # Or an array slice through subtype indication
                    Entity.params._.at(0)._.expr.cast(Name).then(
                        lambda name: And(
                            name.xref_no_overloading,
                            TypeBind(Self.type_var, real_typ)
                        ),
                        default_val=LogicFalse()
                    )
                ),

                # Explicit slice access
                lambda bo=T.BinOp:
                atd.indices.constrain_index_expr(bo.left, 0)
                & atd.indices.constrain_index_expr(bo.right, 0)
                & TypeBind(bo.type_var, bo.right.type_var)
                & TypeBind(Self.type_var, real_typ)
                & bo.left.sub_equation
                & bo.right.sub_equation,

                # Range attribute
                lambda ar=T.AttributeRef:
                ar.sub_equation
                & atd.indices.constrain_index_expr(ar, 0)
                & TypeBind(Self.type_var, real_typ),

                # Subtype indication
                lambda st=T.SubtypeIndication:
                st.sub_equation
                & TypeBind(Self.type_var, real_typ),

                lambda _: LogicFalse(),
            ),

            # Type has user defined indexing
            Not(typ.is_null) & typ.has_ud_indexing,
            typ.constant_indexing_fns.concat(typ.variable_indexing_fns)
            .logic_any(lambda fn: Let(
                lambda
                formal=fn.subp_spec_or_null.unpacked_formal_params.at(1),
                ret_type=fn.subp_spec_or_null.return_type,
                param=Entity.params.at(0).expr:

                TypeBind(Self.type_var, ret_type)
                & If(constrain_params,
                     param.sub_equation, LogicTrue())
                & TypeBind(param.type_var, formal.spec.formal_type)
            )),

            LogicFalse()
        )

    @langkit_property(return_type=Equation, dynamic_vars=[env, origin])
    def subprogram_equation(subp_spec=T.BaseFormalParamHolder.entity,
                            dottable_subp=Bool,
                            primitive=AdaNode):

        prim_type = Var(primitive.cast_or_raise(T.BaseTypeDecl))

        return subp_spec.then(
            lambda subp_spec:
            # The type of the expression is the expr_type of the
            # subprogram.
            TypeBind(Self.type_var, subp_spec.cast(BaseSubpSpec)._.return_type)

            # This node represents a call to a subprogram which specification
            # is given by ``subp_spec``.
            & Bind(Self.subp_spec_var, subp_spec)

            # For each parameter, the type of the expression matches
            # the expected type for this subprogram.
            & subp_spec.match_param_list(
                Entity.params, dottable_subp
            ).logic_all(
                lambda pm: If(
                    pm.has_matched,
                    pm.actual.assoc.expr.call_argument_equation(
                        pm.formal.spec.type_expression.designated_type,
                        prim_type
                    ) & If(
                        # Bind actuals designators to parameters if there
                        # are designators.
                        pm.actual.name.is_null,
                        LogicTrue(),
                        Bind(
                            pm.actual.name.ref_var,
                            Let(lambda n=pm.formal.spec: entity_no_md(
                                AdaNode,
                                n.node,
                                n.info.rebindings,
                                n.info.from_rebound
                            ))
                        )
                    ),
                    LogicFalse()
                )
            ),
            default_val=LogicFalse()
        )

    @langkit_property(return_type=Bool, dynamic_vars=[env, origin])
    def check_for_type(typ=T.BaseTypeDecl.entity):
        """
        Check that self is an appropriate CallExpr for given type, which must
        be a subscriptable type (eg; a type for which it makes senses to do a
        call expr on an instance of the type, like an array type, or an access
        to subprogram type.
        """
        # Algorithm: We're Recursing down call expression and component types
        # up to self, checking for each level that the call expression
        # corresponds.

        atd = Var(typ.then(lambda t: t.array_def_with_deref))

        return bind_origin(Self, typ.then(lambda typ: And(
            Or(
                # Arrays
                atd.then(lambda _: Self.suffix.match(
                    # Array indexing case
                    lambda al=AssocList: atd.array_ndims == al.length,

                    # Array slice case
                    lambda _=BinOp: atd.array_ndims == 1,
                    lambda _: False
                ), default_val=False),

                # Accesses to subprograms
                typ.access_def.cast(T.AccessToSubpDef).then(
                    lambda sa:
                    sa.subp_spec.is_matching_param_list(Entity.params, False)
                ),

                # Types with user defined indexing
                typ.has_ud_indexing
                & Self.suffix.cast(T.AssocList).then(lambda al: al.length == 1)
            ),

            Entity.parent.cast(T.CallExpr).then(
                # Since the result type of Self is ``typ``, the result type of
                # its parent CallExpr (if it exists) must be the component type
                # of ``typ`` (we use subscript=True because a CallExpr will
                # dereference implicitly).
                lambda ce: ce.check_for_type(typ.comp_type(is_subscript=True)),

                # We are done if the parent is not a CallExpr. We could
                # actually do more here by considering ExplicitDerefs, but
                # this should be sufficient for the current purpose of
                # check_for_type (e.g. to preemptively discard inadequate
                # candidates in env_elements_baseid).
                default_val=True
            )
        )))


class ParamAssoc(BasicAssoc):
    """
    Assocation (X => Y) used for aggregates and parameter associations.
    """
    designator = Field(type=T.AdaNode)
    r_expr = Field(type=T.Expr)

    expr = Property(Entity.r_expr)
    names = Property(If(Self.designator.is_null,
                        No(T.AdaNode.array), Self.designator.singleton))


class AggregateAssoc(BasicAssoc):
    """
    Assocation (X => Y) used for aggregates and parameter associations.
    """
    designators = Field(type=T.AlternativesList)
    r_expr = Field(type=T.Expr)

    expr = Property(Entity.r_expr)
    names = Property(Self.designators.map(lambda d: d))


class MultiDimArrayAssoc(AggregateAssoc):
    """
    Association used for multi-dimension array aggregates.
    """

    pass


class ParamActual(Struct):
    """
    Data structure used by zip_with_params property. Associates an expression
    (the actual) to a formal param declaration (the parameter).
    """
    param = UserField(type=T.DefiningName.entity)
    actual = UserField(type=T.Expr.entity)


class AssocList(BasicAssoc.list):
    """
    List of associations.
    """

    @langkit_property()
    def unpacked_params():
        """
        Given the list of ParamAssoc, that can in certain case designate
        several actual parameters at once, create an unpacked list of
        SingleActual instances.
        """
        return Entity.mapcat(lambda pa: Let(lambda names=pa.names: If(
            names.length == 0,
            SingleActual.new(name=No(Identifier), assoc=pa).singleton,
            names.filtermap(
                lambda i: SingleActual.new(name=i.cast(T.BaseId), assoc=pa),
                lambda n: n.is_a(T.BaseId),
            )
        )))

    @langkit_property(public=True, return_type=ParamActual.array,
                      dynamic_vars=[default_imprecise_fallback()])
    def zip_with_params():
        """
        Returns an array of pairs, associating formal parameters to actual
        expressions. The formals to match are retrieved by resolving the call
        which this AssocList represents the actuals of.
        """
        # Bind imprecise_fallback to False for now because
        # first_corresponding_decl is not implemented on CallExpr.
        is_dottable_subp = Var(imprecise_fallback.bind(
            False, Entity.parent.cast(T.Name).then(lambda e: e.is_dot_call)
        ))

        params = Var(Entity.parent._.match(
            lambda e=T.CallExpr: e.called_subp_spec._.abstract_formal_params,

            lambda i=T.GenericInstantiation:
            i.generic_entity_name.referenced_decl.cast(T.GenericDecl)
            ._.formal_part.abstract_formal_params,

            lambda c=T.DiscriminantConstraint:
            c.subtype.cast(T.TypeDecl)._.discriminants
            ._.abstract_formal_params,

            lambda a=T.BaseAggregate: origin.bind(Self, env.bind(
                Self.node_env,
                a.expression_type.record_def
                ._.components.abstract_formal_params_for_assocs(Entity),
            )),

            lambda _: No(T.BaseFormalParamDecl.entity.array)
        ))

        return params.then(
            lambda _: Self.match_formals(params, Entity, is_dottable_subp).map(
                lambda m:
                ParamActual.new(
                    param=m.formal.name,
                    actual=m.actual.assoc.expr
                ),
            )
        )


class DeclList(AdaNode.list):
    """
    List of declarations.
    """
    pass


class StmtList(AdaNode.list):
    """
    List of statements.
    """
    pass


class ExplicitDeref(Name):
    """
    Explicit dereference expression (``.all``).
    """

    prefix = Field(type=T.Name)
    ref_var = Property(Self.prefix.ref_var)

    r_called_spec = UserField(LogicVar, public=False)

    subp_spec_var = Property(Self.r_called_spec)
    defines_subp_spec_var = Property(True)

    relative_name = Property(Entity.prefix.relative_name)

    @langkit_property()
    def designated_env():
        # Since we have implicit dereference in Ada, everything is directly
        # accessible through the prefix, so we just use the prefix's env.
        return Entity.prefix.designated_env

    @langkit_property()
    def env_elements_impl():
        return bind_origin(
            Self,
            Entity.prefix.env_elements_impl.filter(
                # Env elements for access derefs need to be of an access type
                lambda e:
                e.cast(BasicDecl)._.expr_type.then(lambda t: t.is_access_type)
            )
        )

    @langkit_property(return_type=Equation, dynamic_vars=[env, origin])
    def eq_for_type(typ=T.BaseTypeDecl.entity):
        return And(
            TypeBind(Self.prefix.type_var, typ),
            TypeBind(Self.type_var, typ.accessed_type)
        )

    @langkit_property()
    def xref_equation():
        return Entity.bottom_up_name_equation

    @langkit_property(return_type=Equation, dynamic_vars=[env, origin])
    def general_xref_equation(root=(T.Name, No(T.Name))):
        env_els = Var(Entity.env_elements)

        return Entity.prefix.sub_equation & env_els.logic_any(
            lambda el: el.cast(T.BasicDecl).expr_type.then(
                lambda typ:

                # Bind Self's ref var to the entity, with the access_entity
                # field set to False, since self designated the non-access
                # entity.
                Bind(Self.ref_var, el.trigger_access_entity(False))

                & Bind(Self.ref_var, Self.prefix.ref_var)
                & Entity.parent_name_equation(typ, root),
                default_val=LogicFalse()
            )
        )


class BoxExpr(Expr):
    """
    Box expression (``<>``).
    """
    xref_equation = Property(LogicTrue())


class OthersDesignator(AdaNode):
    """
    ``other`` designator.
    """
    xref_equation = Property(LogicTrue())


class IfExpr(Expr):
    """
    ``if`` expression.
    """

    cond_expr = Field(type=T.Expr)
    then_expr = Field(type=T.Expr)
    alternatives = Field(type=T.ElsifExprPart.list)
    else_expr = Field(type=T.Expr)

    @langkit_property()
    def xref_equation():
        return (
            # Construct sub equations for common sub exprs
            Entity.cond_expr.sub_equation
            & Entity.then_expr.sub_equation

            & If(
                Not(Self.else_expr.is_null),

                # If there is an else, then construct sub equation
                Entity.else_expr.sub_equation
                # And bind the then expr's and the else expr's types
                & TypeBind(Self.then_expr.type_var, Self.else_expr.type_var),

                # If no else, then the then_expression has type bool
                bool_bind(Self.then_expr.type_var)
            ) & Entity.alternatives.logic_all(lambda elsif: (
                # Build the sub equations for cond and then exprs
                elsif.cond_expr.sub_equation
                & elsif.then_expr.sub_equation

                # The condition is boolean
                & bool_bind(elsif.cond_expr.type_var)

                # The elsif branch then expr has the same type as Self's
                # then_expr.
                & TypeBind(Self.then_expr.type_var, elsif.then_expr.type_var)
            )) & bool_bind(Self.cond_expr.type_var)
            & TypeBind(Self.then_expr.type_var, Self.type_var)
        )


class ElsifExprPart(AdaNode):
    """
    ``elsif`` block, part of an ``if`` expression.
    """
    cond_expr = Field(type=T.Expr)
    then_expr = Field(type=T.Expr)


class CaseExpr(Expr):
    """
    ``case`` expression.
    """
    expr = Field(type=T.Expr)
    cases = Field(type=T.CaseExprAlternative.list)

    @langkit_property()
    def xref_equation():
        # We solve Self.expr separately because it is not dependent on the rest
        # of the semres.
        ignore(Var(Entity.expr.resolve_names_internal(
            True, Predicate(BaseTypeDecl.is_discrete_type, Self.expr.type_var)
        )))

        return Entity.cases.logic_all(lambda alt: (
            alt.choices.logic_all(lambda c: c.match(
                # Expression case
                lambda e=T.Expr:
                TypeBind(e.type_var, Self.expr.type_val)
                & e.sub_equation,

                # TODO: Bind other cases: SubtypeIndication and Range
                lambda _: LogicTrue()
            ))

            # Equations for the dependent expressions
            & alt.expr.sub_equation

            # The type of self is the type of each expr. Also, the type of
            # every expr is bound together by the conjunction of this bind for
            # every branch.
            & TypeBind(Self.type_var, alt.expr.type_var)
        ))


class CaseExprAlternative(Expr):
    """
    Alternative in a ``case`` expression (``when ... => ...``).
    """
    choices = Field(type=T.AlternativesList)
    expr = Field(type=T.Expr)


@abstract
class SingleTokNode(Name):
    """
    Base class for nodes that are made up of a single token.
    """

    token_node = True

    relative_name = Property(Entity)

    r_ref_var = UserField(LogicVar, public=False)
    """
    This field is the logic variable for this node. It is not used directly,
    instead being retrieved via the ref_var property
    """

    ref_var = Property(Self.r_ref_var)

    r_called_spec = UserField(LogicVar, public=False)

    subp_spec_var = Property(Self.r_called_spec)
    defines_subp_spec_var = Property(True)

    sym = Property(
        Self.symbol, doc="Shortcut to get the symbol of this node"
    )

    @langkit_property()
    def env_get_first(lex_env=LexicalEnv, lookup_type=LK, from_node=T.AdaNode):
        """
        Like env.get_first, but returning the first visible element in the Ada
        sense.
        """
        return env_get(
            lex_env,
            Self,
            lookup=lookup_type,
            from_node=from_node,
            categories=noprims
        ).find(lambda el: Self.has_visibility(el))


class DefiningName(Name):
    """
    Name that defines an entity.
    """
    name = Field(type=T.Name)

    parent_scope = Property(Self.name.parent_scope)
    scope = Property(Self.name.scope)
    relative_name = Property(Entity.name.relative_name)
    ref_var = Property(Self.name.ref_var)
    env_elements_impl = Property(Entity.name.env_elements_impl)

    @langkit_property()
    def all_env_els_impl(seq=(Bool, True),
                         seq_from=(AdaNode, No(T.AdaNode))):
        return Entity.name.all_env_els_impl(seq, seq_from)

    basic_decl = Property(
        Self.parents.find(lambda p: p.is_a(T.BasicDecl))
        .cast_or_raise(T.BasicDecl).as_entity,
        public=True, memoized=True,
        doc="Returns this DefiningName's basic declaration"
    )

    @langkit_property(return_type=Bool,
                      dynamic_vars=[default_imprecise_fallback()])
    def is_referenced_by(x=AdaNode.entity):
        """
        Returns True iff the given node is an identifier referring to Self.
        Note that this takes into account both direct references as well as
        potential references.

        Potential references can occur in the context of dispatching calls: an
        identifier having for direct reference the declaration of an
        overridable subprogram is considered a potential reference to all
        subprograms that override it if the identifier appears in a dispatching
        call.
        """
        return And(
            x.match(lambda i=BaseId: Self.name_is(i.name_symbol),
                    lambda _: False),
            Let(lambda canon=x.xref._.canonical_part._.node: Or(
                # Either `x` is a direct reference
                canon == Self,

                # Or `x` refers to one of the base subprograms of defined by
                # Self, and `x` appears in a dispatching call context.
                Entity.basic_decl.base_subp_declarations.then(
                    lambda decls: And(
                        decls.any(lambda d: d.defining_name.node == canon),
                        x.cast(Name).is_dispatching_call
                    )
                )
            ))
        )

    @langkit_property(public=True, return_type=T.BaseId.entity.array,
                      dynamic_vars=[default_imprecise_fallback()])
    def find_all_refs_in(x=AdaNode.entity, origin=AdaNode):
        """
        Searches all references to this defining name in the given node and its
        children.
        """
        return x.children.then(
            lambda c: c.filter(lambda n: Not(n.is_null | (n.node == origin)))
            .mapcat(lambda n: Entity.find_all_refs_in(n, origin))
        ).concat(If(
            Entity.is_referenced_by(x),
            x.cast_or_raise(BaseId).singleton,
            No(BaseId.entity.array)
        ))

    @langkit_property(public=True, return_type=T.BaseId.entity.array,
                      dynamic_vars=[default_imprecise_fallback()])
    def find_all_references(units=AnalysisUnit.array):
        """
        Searches all references to this defining name in the given list of
        units.
        """
        dn = Var(Entity.canonical_part)

        # If `dn` defines a subprogram which overrides some subprogram P, we
        # need to do the unit filtering from the declaration of P so that we
        # don't omit units in which we may have potential references to Self
        # through dispatching calls. This is valid because all units that would
        # import `dn` will necessarily import `base` as well, as `dn`
        # necessarily imports `base` to define its overriding subprogram.
        # This only works if filter_is_imported_by is called with transitive
        # set to True.
        bases = Var(origin.bind(
            Self,
            dn.basic_decl.root_subp_declarations._or(dn.basic_decl.singleton)
        ))

        all_units = Self.unique_units(bases.mapcat(
            lambda base: base.filter_is_imported_by(units, True)
        ))

        return all_units.mapcat(
            lambda u: u.root.then(
                lambda r: dn.find_all_refs_in(r.as_bare_entity, Self)
            )
        )

    @langkit_property()
    def find_matching_name(bd=BasicDecl.entity):
        """
        Helper for navigation proxies. Will return the defining name matching
        Self on the given BasicDecl.
        """
        return bd.defining_names.find(
            lambda di: Entity.name.name_is(di.name_symbol)
        )

    @langkit_property(public=True, return_type=T.BaseId.entity.array,
                      dynamic_vars=[default_imprecise_fallback()])
    def is_called_by(units=AnalysisUnit.array):
        """
        Return the list of all possible calls to the subprogram which Self is
        the defining name of.

        .. note:: This currently only handles direct calls. Need to add support
           for: dispatching calls, access-to-subprograms and calls done inside
           generics.
        """
        return Entity.find_all_references(units).filter(
            lambda r: r.is_direct_call
        )

    next_part = Property(
        Entity.find_matching_name(Entity.basic_decl.next_part_for_decl),
        public=True,
        doc="Like ``BasicDecl.next_part_for_decl`` on a defining name"
    )

    previous_part = Property(
        Entity.find_matching_name(Entity.basic_decl.previous_part_for_decl),
        public=True,
        doc="Like ``BasicDecl.previous_part_for_decl`` on a defining name"
    )

    canonical_part = Property(
        Entity.find_matching_name(Entity.basic_decl.canonical_part),
        public=True,
        doc="Like ``BasicDecl.canonical_part`` on a defining name"
    )

    @langkit_property()
    def xref_equation():
        # The name field of a defining name must be an Identifier or a
        # DottedName. So we can special case the construction of the xref
        # equation here.
        return Entity.name.cast(T.DottedName).then(
            lambda dn: dn.prefix.xref_equation,
            default_val=LogicTrue()
        )

    # There are names to resolve in a defining name only if its name field is
    # a dotted name, in which case we must resolve its prefix.
    xref_entry_point = Property(Self.name.is_a(T.DottedName))


class EndName(Name):
    """
    Entity name in ``end ...;`` syntactic constructs.
    """

    name = Field(type=T.Name)

    parent_scope = Property(Self.name.parent_scope)
    scope = Property(Self.name.scope)
    relative_name = Property(Entity.name.relative_name)
    ref_var = Property(Self.name.ref_var)
    env_elements_impl = Property(Entity.name.env_elements_impl)

    basic_decl = Property(
        Self.parents.find(lambda p: p.is_a(T.NamedStmt)).then(
            lambda ns:
            ns.cast_or_raise(T.NamedStmt).decl.cast(T.BasicDecl).as_entity
        )._or(
            Self.parents.find(lambda p: p.is_a(T.BasicDecl))
            .cast_or_raise(T.BasicDecl).as_entity,
        ),
        public=True, memoized=True,
        doc="Returns this EndName's basic declaration"
    )

    xref_equation = Property(If(
        Entity.basic_decl.is_a(T.SubpBody),
        Bind(Self.ref_var, Entity.basic_decl),
        Entity.name.xref_no_overloading,
    ))

    xref_entry_point = Property(True)


@abstract
class BaseId(SingleTokNode):
    """
    Base class for identifiers.
    """

    annotations = Annotations(custom_short_image=True)

    @langkit_property(memoized=True)
    def scope():
        elt = Var(env.get_first(Self, categories=noprims))
        ret = Var(If(
            Not(elt.is_null) & elt.node.is_a(
                T.BasicDecl
            ),
            elt.children_env,
            EmptyEnv
        ))

        # If this the corresponding decl is a generic, go grab the internal
        # package decl.
        return ret.env_node.cast(T.GenericPackageDecl).then(
            lambda gen_pkg_decl: gen_pkg_decl.package_decl.children_env,
            default_val=ret
        )

    @langkit_property()
    def designated_env_no_overloading():
        return Var(Self.env_get_first(
            env,
            lookup_type=If(Self.is_prefix, LK.recursive, LK.flat),
            from_node=If(Self.in_aspect, No(T.AdaNode), Self)
        )).cast(T.BasicDecl).then(
            lambda bd: If(
                bd._.is_package, Entity.pkg_env(bd), bd.defining_env
            )
        )

    @langkit_property(dynamic_vars=[env, origin])
    def designated_env():
        """
        Decoupled implementation for designated_env, specifically used by
        DottedName when the parent is a library level package.
        """
        bd = Var(Self.parents.find(
            lambda p: p.is_a(GenericPackageInstantiation)
        ))

        env_el = Var(Self.env_get_first(
            env,
            lookup_type=If(Self.is_prefix, LK.recursive, LK.flat),
            from_node=If(Self.in_aspect, No(T.AdaNode), Self),
        )).cast(T.BasicDecl)

        return If(
            # If first element is a package, then return the pkg env
            env_el._.is_package & Not(env_el.node == bd),
            Entity.pkg_env(env_el),

            Entity.env_elements_baseid.then(
                lambda all_env_els:
                all_env_els.filter(lambda e: And(
                    # Exclude own generic package instantiation from the lookup
                    Not(e.node == bd),

                    Self.has_visibility(e)
                ),
                )
            ).map(lambda e: e.cast(BasicDecl).defining_env).env_group(),
        )

    @langkit_property(dynamic_vars=[env, origin])
    def pkg_env(from_pkg=T.BasicDecl.entity):
        """
        Return the lexical environment for this identifier, should it be a
        package. This method handles resolving to the most visible part of a
        package - private or body - if necessary. It also unwinds package
        renamings if necessary.

        If ``inst_from_formal`` is True, we know that bd is a generic package
        instantiation coming from a rebound formal package, and that we need
        visibility on the formals.
        """

        # If the given package is a renaming (after potentially several levels
        # of renamings) of another package P, do the rest of the work on P
        # instead.
        pkg = Var(from_pkg.cast(PackageRenamingDecl).then(
            lambda r: r.final_renamed_package,
            default_val=from_pkg
        ))

        bd = Var(
            # If pkg is a generic package (non instantiated) and it is
            # rebound somewhere in the context of Self's rebindings, then
            # we want to put back those rebindings on it, because it means
            # we are inside a generic instantiation, so refering to the
            # generic package actually means referring to the
            # instantiation.
            pkg.unshed_rebindings(Entity.info.rebindings)
        )
        is_inst_from_formal = Var(pkg.is_a(T.GenericPackageInstantiation) &
                                  from_pkg.info.from_rebound)

        env = Var(If(
            bd.is_a(T.GenericPackageInstantiation) & is_inst_from_formal,
            bd.cast(T.GenericPackageInstantiation).defining_env_impl(True),
            bd.defining_env
        ))

        # If the basic_decl is a package decl with a private part, we get it.
        # Else we keep the defining env.
        private_part_env = Var(
            env.get('__privatepart', LK.flat, categories=noprims).at(0).then(
                lambda pp: pp.children_env, default_val=env
            )
        )

        package_body_env = Var(
            private_part_env.get('__nextpart', LK.flat, categories=noprims)
            .at(0).then(
                lambda pb: If(
                    # If the package is implemented as a separate, we need to
                    # jump through one more link to get to the body.
                    pb.is_a(PackageBodyStub),

                    pb.children_env
                    .get('__nextpart', LK.flat, categories=noprims)
                    .at(0).then(lambda pb: pb.children_env),

                    pb.children_env
                ), default_val=EmptyEnv
            )
        )

        return Cond(

            # If we're looking from the body, return a group of all the
            # relevant envs together.
            Not(package_body_env.equals(EmptyEnv))
            & Self.is_children_env(package_body_env,
                                   (origin._or(Self)).node_env),
            Array([package_body_env, private_part_env, env]).env_group(),

            # If we're looking from the private part, return a group of private
            # part + public part.
            Self.is_children_env(private_part_env,
                                 (origin._or(Self)).node_env),
            Array([private_part_env, env]).env_group(),

            # TODO: Probably some special handling for separates here, because
            # they'll have full visibility on the package body in which they're
            # defined.

            env
        )

    parent_scope = Property(env)

    @langkit_property()
    def designated_type_impl():

        def get_real_type(basic_decl):
            return basic_decl.match(
                lambda t=T.BaseTypeDecl.entity: t,
                lambda tb=T.TaskBody.entity: tb.task_type,
                lambda _: No(BaseTypeDecl.entity)
            )

        # This is the view of the type where it is referenced
        des_type_1 = Var(Self.env_get_first(
            env,
            from_node=Self,
            lookup_type=If(Self.is_prefix, LK.recursive, LK.flat),
        ).then(
            lambda env_el: get_real_type(env_el)
        ))

        # This is the view of the type where it is used
        des_type_2 = Var(Self.env_get_first(
            env,
            from_node=origin,
            lookup_type=If(Self.is_prefix, LK.recursive, LK.flat),
        ).then(
            lambda env_el: get_real_type(env_el)
        ))

        des_type = Var(Cond(
            # In some cases des_type_1 can be null TODO: investigate
            des_type_1.is_null, des_type_2,

            # If same type, then it doesn't matter (return early from the view
            # checking below).
            des_type_1 == des_type_2, des_type_1,

            # If des_type_1 is a less complete version of des_type_2, then pick
            # des_type_2.
            des_type_1.then(lambda d: d.is_view_of_type(des_type_2)),
            des_type_2,

            # In any other case use des_type_1
            des_type_1
        ))

        # We might have a more complete view of the type at the origin point
        completer_view = Var(origin.then(lambda o: env_get_first(
            o.children_env, Self, from_node=origin, categories=noprims
        )).cast(T.BaseTypeDecl))

        # If completer_view is a more complete view of the type we're looking
        # up, then return completer_view. Else return des_type.
        return If(
            Not(completer_view.is_null)
            & des_type.then(lambda d: d.is_view_of_type(completer_view)),
            completer_view,
            des_type
        )

    @langkit_property(dynamic_vars=[env])
    def env_elements_impl():
        return Entity.env_elements_baseid

    @langkit_property()
    def all_env_els_impl(seq=(Bool, True),
                         seq_from=(AdaNode, No(T.AdaNode))):
        return env_get(
            env,
            Self,
            lookup=If(Self.is_prefix, LK.recursive, LK.flat),
            from_node=If(seq, If(Not(seq_from.is_null), seq_from, Self),
                         No(T.AdaNode))
        )

    @langkit_property(dynamic_vars=[env], memoized=True)
    def env_elements_baseid():
        """
        Decoupled implementation for env_elements_impl, specifically used by
        designated_env when the parent is a library level package.
        """
        items = Var(env_get(
            env,
            Self,
            lookup=If(Self.is_prefix, LK.recursive, LK.flat),
            # If we are in an aspect, then lookup is not sequential.
            # TODO: The fact that this is here is ugly, and also the logic is
            # probably wrong.
            from_node=If(Self.in_aspect, No(T.AdaNode), Self)
        ))

        # TODO: there is a big smell here: We're doing the filtering for parent
        # expressions in the baseid env_elements. We should solve that.

        pc = Var(Entity.parent_callexpr)

        return bind_origin(Self, Cond(
            pc.is_null,

            # If it is not the main id in a CallExpr: either the name
            # designates something else than a subprogram, either it designates
            # a subprogram that accepts no explicit argument. So filter out
            # other subprograms.
            items.filter(lambda e: (

                # If there is a subp_spec, check that it corresponds to
                # a parameterless subprogram.
                Or(
                    e.cast_or_raise(BasicDecl).can_be_paramless,
                    e.cast(T.BaseSubpBody)._.in_scope
                )
            )),

            # This identifier is the name for a called subprogram or an array.
            # So only keep:
            # * subprograms for which the actuals match
            # * arrays for which the number of dimensions match
            # * any type that has a user defined indexing aspect.

            pc.suffix.cast(AssocList).then(lambda params: (
                items.filter(lambda e: e.match(
                    # Type conversion case
                    lambda _=BaseTypeDecl: params.length == 1,

                    lambda b=BasicDecl:
                    b.subp_spec_or_null.then(
                        lambda spec: Let(
                            lambda real_pc=If(
                                spec.cast(T.EntrySpec)._.family_type.is_null,
                                pc, pc.parent.cast_or_raise(T.CallExpr)
                            ):

                            # Either the subprogram is matching the CallExpr's
                            # parameters.
                            And(
                                spec.is_matching_param_list(
                                    params, b.info.md.dottable_subp
                                ),
                                real_pc.parent.cast(T.CallExpr).then(
                                    lambda ce: ce.check_for_type(b.expr_type),
                                    default_val=True
                                )
                            )

                            # Or the entity is parameterless, and the returned
                            # component (s) matches the callexpr (s).
                            | And(real_pc.check_for_type(b.expr_type),
                                  spec.paramless(b.info.md.dottable_subp)),

                        ),
                        # In the case of ObjectDecls/CompDecls in general,
                        # verify that the callexpr is valid for the given
                        # type designator.
                        default_val=pc.check_for_type(b.expr_type)
                    ),

                    lambda _: False
                ))
            ), default_val=items)
        ))

    @langkit_property()
    def xref_equation():
        return Entity.base_id_xref_equation()

    @langkit_property(return_type=Equation, dynamic_vars=[env, origin])
    def base_id_xref_equation():
        env_els = Var(Entity.env_elements)

        return (
            Self.ref_var.domain(env_els)
            & Bind(Self.ref_var, Self.type_var, BasicDecl.expr_type,
                   eq_prop=BaseTypeDecl.matching_type)

            # If this BaseId represents a call, the called subprogram will be
            # held in Self.ref_var, in which case subp_spec_or_null will
            # return the specification of the called subprogram. If ref_var
            # does not contain a subprogram, this BaseId cannot be a call,
            # and subp_spec_or_null would indeed return null in this case.
            & Bind(Self.ref_var, Self.subp_spec_var,
                   conv_prop=BasicDecl.subp_spec_or_null)
        )


class Op(BaseId):
    """
    Operation in a binary expression.

    Note that the ARM does not consider "double_dot" ("..") as a binary
    operator, but we process it this way here anyway to keep things simple.
    """
    enum_node = True
    alternatives = ["and", "or", "or_else", "and_then", "xor", "in",
                    "not_in", "abs", "not", "pow", "mult", "div", "mod",
                    "rem", "plus", "minus", "concat", "eq", "neq", "lt",
                    "lte", "gt", "gte", "double_dot"]

    @langkit_property(return_type=T.Symbol)
    def subprogram_symbol():
        """
        Return the symbol that needs to be used to define an overload of this
        operator.
        """
        return Self.match(
            lambda _=Op.alt_and:    '"and"',
            lambda _=Op.alt_or:     '"or"',
            lambda _=Op.alt_xor:    '"xor"',
            lambda _=Op.alt_abs:    '"abs"',
            lambda _=Op.alt_not:    '"not"',
            lambda _=Op.alt_pow:    '"**"',
            lambda _=Op.alt_mult:   '"*"',
            lambda _=Op.alt_div:    '"/"',
            lambda _=Op.alt_mod:    '"mod"',
            lambda _=Op.alt_rem:    '"rem"',
            lambda _=Op.alt_plus:   '"+"',
            lambda _=Op.alt_minus:  '"-"',
            lambda _=Op.alt_concat: '"&"',
            lambda _=Op.alt_eq:     '"="',
            lambda _=Op.alt_neq:    '"/="',
            lambda _=Op.alt_lt:     '"<"',
            lambda _=Op.alt_lte:    '"<="',
            lambda _=Op.alt_gt:     '">"',
            lambda _=Op.alt_gte:    '">="',
            lambda _:               '<<>>',
        )

    @langkit_property(return_type=T.BasicDecl.entity.array)
    def subprograms_for_symbol(sym=T.Symbol, from_node=T.AdaNode.entity):
        return from_node.node_env.get(sym).filtermap(
            lambda e: e.cast_or_raise(T.BasicDecl),
            lambda e: e.cast_or_raise(T.BasicDecl).is_subprogram
        )

    subprograms = Property(
        Self.subprograms_for_symbol(Self.subprogram_symbol, Entity),
        doc="""
        Return the subprograms corresponding to this operator accessible in the
        lexical environment.
        """
    )

    name_symbol = Property(Self.subprogram_symbol)

    @langkit_property()
    def xref_equation():
        # An Op can only be a field of a BinOp or UnOp, so its ref var will
        # be bound in the xref equations of these two types.
        return LogicFalse()


@has_abstract_list
class Identifier(BaseId):
    """
    Regular identifier.
    """

    annotations = Annotations(repr_name="Id")
    is_not_class_id = Property(Not(Self.symbol == 'Class'))


class StringLiteral(BaseId):
    """
    String literal.
    """

    annotations = Annotations(repr_name="Str")

    @langkit_property(return_type=T.String, external=True, public=True,
                      uses_entity_info=False, uses_envs=False)
    def denoted_value():
        """
        Return the value that this literal denotes.
        """
        pass

    @langkit_property()
    def xref_equation():
        return If(
            # StringLiteral can be in a name, if it is an operator, in which
            # case we don't want to constrain its type.
            Self.parent.is_a(Name),
            Entity.base_id_xref_equation,
            Or(
                TypeBind(Self.type_var, Self.std_entity('String')),
                Predicate(BaseTypeDecl.is_str_type_or_null, Self.type_var)
            )
        )


class EnumLiteralDecl(BasicSubpDecl):
    """
    Declaration for an enumeration literal.
    """

    name = Field(type=T.DefiningName)
    aspects = NullField()

    is_static_decl = Property(True)

    @langkit_property(public=True)
    def enum_type():
        """
        Return the enum type corresponding to this enum literal.
        """
        return Self.parents.find(
            lambda p: p.is_a(TypeDecl)
        ).as_entity.cast(TypeDecl)

    defining_names = Property(Entity.name.singleton)

    @langkit_property(memoized=True)
    def synth_type_expr():
        return EnumLitSynthTypeExpr.new().as_entity

    @langkit_property(memoized=True)
    def subp_decl_spec():
        return T.EnumSubpSpec.new().as_entity

    env_spec = EnvSpec(

        add_to_env_kv(Self.name_symbol, Self,
                      dest_env=Entity.enum_type.node_env),

        add_to_env_kv(
            Self.name_symbol, Self,
            dest_env=Entity.enum_type.primitives,
            metadata=new_metadata(primitive=Entity.enum_type.node)
        )
    )


class CharLiteral(BaseId):
    """
    Character literal.
    """

    annotations = Annotations(repr_name="Chr")

    @langkit_property(return_type=T.Character, external=True, public=True,
                      uses_entity_info=False, uses_envs=False)
    def denoted_value():
        """
        Return the value that this literal denotes.
        """
        pass

    @langkit_property()
    def xref_equation():
        return Predicate(BaseTypeDecl.is_not_null_char_type, Self.type_var)


@abstract
class NumLiteral(SingleTokNode):
    """
    Base class for number literals.
    """

    annotations = Annotations(repr_name="Num")


class RealLiteral(NumLiteral):
    """
    Literal for a real number.
    """

    annotations = Annotations(repr_name="Real")

    @langkit_property()
    def xref_equation():
        return universal_real_bind(Self.type_var)


class IntLiteral(NumLiteral):
    """
    Literal for an integer.
    """

    annotations = Annotations(repr_name="Int")

    @langkit_property()
    def xref_equation():
        return universal_int_bind(Self.type_var)

    @langkit_property(return_type=T.BigInt, external=True, public=True,
                      uses_entity_info=False, uses_envs=False)
    def denoted_value():
        """
        Return the value that this literal denotes.
        """
        pass


class NullLiteral(SingleTokNode):
    """
    The ``null`` literal.
    """

    annotations = Annotations(repr_name="Null")

    @langkit_property()
    def xref_equation():
        return Predicate(BaseTypeDecl.is_access_type, Self.type_var)


class SingleFormal(Struct):
    name = UserField(type=DefiningName.entity)
    spec = UserField(type=BaseFormalParamDecl.entity)


class SingleActual(Struct):
    name = UserField(type=BaseId)
    assoc = UserField(type=T.BasicAssoc.entity)


class ParamMatch(Struct):
    """
    Helper data structure to implement SubpSpec/ParamAssocList matching.

    Each value relates to one ParamAssoc.
    """
    has_matched = UserField(type=Bool, doc="""
        Whether the matched ParamAssoc a ParamSpec.
    """)
    actual = UserField(type=SingleActual)
    formal = UserField(type=SingleFormal)


@abstract
class BaseSubpSpec(BaseFormalParamHolder):
    """
    Base class for subprogram specifications.
    """

    name = AbstractProperty(type=T.DefiningName, ignore_warn_on_node=True)
    returns = AbstractProperty(
        type=T.TypeExpr.entity, public=True, doc="""
        Syntax property. Return the type expression node corresponding to the
        return of this subprogram spec.
        """
    )

    params = AbstractProperty(
        type=T.ParamSpec.entity.array, public=True, doc="""
        Returns the array of parameters specification for this subprogram spec.
        """
    )

    abstract_formal_params = Property(
        Entity.params.map(lambda p: p.cast(BaseFormalParamDecl))
    )

    @langkit_property(return_type=Bool)
    def match_return_type(other=T.BaseSubpSpec.entity):
        # Check that the return type is the same. Caveat: it's not because
        # we could not find the canonical type that it is null!
        #
        # TODO: simplify this code when SubpSpec provides a kind to
        # distinguish functions and procedures.
        self_ret = Var(bind_origin(Self, Entity.return_type))
        other_ret = Var(bind_origin(other.node, other.return_type))
        return Or(
            And(other_ret.is_null, self_ret.is_null),
            And(
                Not(other_ret.is_null), Not(self_ret.is_null),
                bind_origin(Self, self_ret.matching_type(other_ret))
            )
        )

    @langkit_property(return_type=Bool)
    def match_formal_params(other=T.BaseSubpSpec.entity,
                            match_names=(Bool, True)):
        # Check that there is the same number of formals and that each
        # formal matches.
        self_params = Var(Entity.unpacked_formal_params)
        other_params = Var(other.unpacked_formal_params)

        self_types = Var(bind_origin(Self, Entity.param_types))
        other_types = Var(bind_origin(other.node, other.param_types))
        return And(
            self_params.length == other_params.length,
            bind_origin(Self, self_params.all(
                lambda i, p:
                Or(Not(match_names),
                   p.name.matches(other_params.at(i).name.node))
                & self_types.at(i).matching_type(other_types.at(i))
            ))
        )

    @langkit_property(return_type=Bool)
    def match_signature(other=T.BaseSubpSpec.entity, match_name=Bool,
                        use_entity_info=(Bool, True)):
        """
        Return whether SubpSpec's signature matches Self's.

        Note that the comparison for types isn't just a name comparison: it
        compares the canonical types.

        If match_name is False, then the name of subprogram will not be
        checked.

        If use_entity_info is True and Entity's metadata has values for fields
        `primitive` and `primitive_real_type` (e.g. if it was retrieved from a
        primitive_env), those will be taken into account and match_signature
        will return True if `other` overrides `Entity`.
        """
        ent = Var(If(use_entity_info, Entity, Self.as_bare_entity))
        return And(
            # Check that the names are the same
            Not(match_name) | ent.name.matches(other.name),
            ent.match_return_type(other),
            ent.match_formal_params(other, match_name),
        )

    @langkit_property(return_type=LexicalEnv,
                      dynamic_vars=[origin])
    def defining_env():
        """
        Helper for BasicDecl.defining_env.
        """
        return If(Entity.returns.is_null,
                  EmptyEnv, Entity.returns.defining_env)

    @langkit_property(return_type=BaseTypeDecl.entity, dynamic_vars=[origin])
    def potential_dottable_type():
        """
        If self meets the criteria for being a subprogram callable via the dot
        notation, return the type of dottable elements.
        """
        return Entity.params._.at(0)._.type_expr._.element_type

    @langkit_property(return_type=BaseTypeDecl.entity)
    def candidate_type_for_primitive(typ=T.TypeExpr.entity):
        """
        If the given type expression designates a type of which Self is a
        primitive, return that designated type. Otherwise return null.
        """
        bd = Var(Entity.parent.cast_or_raise(BasicDecl))
        tpe = Var(bind_origin(Self, typ.match(
            lambda at=T.AnonymousType: at.element_type._.canonical_type,
            lambda other: other.designated_type._.canonical_type
        )))

        return If(
            tpe._.declarative_scope.then(lambda ds: ds.any_of(
                bd.declarative_scope,
                bd.declarative_scope._.parent.cast(BasePackageDecl)
                ._.public_part
            )),
            tpe,
            No(BaseTypeDecl.entity)
        )

    @langkit_property(return_type=BaseTypeDecl.entity.array, public=True)
    def primitive_subp_of():
        """
        Return the types of which this subprogram is a primitive of.
        """

        # TODO: This might be improved by checking for spelling before looking
        # up every type.

        params = Var(Entity.unpacked_formal_params)
        types = Var(params.map(lambda p: p.spec.type_expression).concat(
            Entity.returns._.singleton
        ))

        return types.map(
            lambda t: Entity.candidate_type_for_primitive(t)
        ).filter(
            lambda t: Not(t.is_null)
        )

    @langkit_property(return_type=BaseTypeDecl.entity, public=True)
    def first_primitive_subp_of():
        """
        Return the first type of which this subprogram is a primitive of.
        """
        return Entity.primitive_subp_of.then(lambda p: p.at(0))

    @langkit_property(return_type=BaseTypeDecl.entity, memoized=True)
    def primitive_subp_of_tagged():
        return Entity.primitive_subp_of.find(
            lambda t: t.full_view.is_tagged_type
        )

    @langkit_property(return_type=BaseTypeDecl.entity.array)
    def dottable_subp_of():
        """
        Returns whether the subprogram containing this spec is a subprogram
        callable via the dot notation.
        """
        bd = Var(Entity.parent.cast_or_raise(BasicDecl))

        return bind_origin(Entity.name, If(
            Entity.nb_max_params > 0,
            Entity.potential_dottable_type.then(lambda t: If(
                # Dot notation only works on tagged types, needs to be declared
                # in the same scope as the type.

                # NOTE: We are not actually implementing the correct Ada
                # semantics here, because you can call primitives via the dot
                # notation on private types with a tagged completion.
                # However, since private types don't have components, this
                # should not ever be a problem with legal Ada.
                Not(t.is_a(BaseSubtypeDecl))
                & t.full_view.is_tagged_type
                & bd.declarative_scope.then(lambda ds: Or(
                    # If the subprogram is defined in the same declarative
                    # scope as t, then it is a dottable subprogram of t.
                    ds == t.declarative_scope,

                    # But in Ada it is also possible to declare a dottable subp
                    # of a type t in a different declarative scope than where
                    # t is defined: for example, in the body the package in
                    # which it is declared, or in its private part. The next
                    # piece of code handles that by comparing the declarative
                    # scope of t with the public/private part of the package
                    # in which the subprogram is declared.
                    ds.as_entity.parent.cast(T.PackageBody).then(
                        lambda pbody: env.bind(
                            pbody.initial_env,
                            pbody.package_previous_part
                            .cast(T.BasePackageDecl).node
                        )
                    )._or(ds.parent.cast(T.BasePackageDecl)).then(
                        lambda pdecl: t.declarative_scope.any_of(
                            pdecl.private_part, pdecl.public_part
                        )
                    )
                )),

                t.singleton,

                No(T.BaseTypeDecl.entity.array)
            )),
            No(T.BaseTypeDecl.entity.array)
        ))

    @langkit_property()
    def real_type(tpe=T.BaseTypeDecl.entity):
        return If(
            Entity.info.md.primitive == tpe.node,

            If(
                Entity.info.md.primitive_real_type.is_null,

                entity_no_md(
                    BaseTypeDecl,
                    tpe.node,
                    Entity.info.rebindings,
                    Entity.info.from_rebound
                ),

                Entity.info.md.primitive_real_type
                .cast(T.PrimTypeAccessor).get_prim_type,
            ),

            tpe
        )

    @langkit_property(return_type=T.BaseTypeDecl.entity.array,
                      dynamic_vars=[default_origin()], public=True)
    def param_types():
        """
        Returns the type of each parameter of Self.
        """
        return Entity.unpacked_formal_params.map(
            lambda fp: Entity.real_type(fp.spec.formal_type)
        )

    @langkit_property(return_type=T.BaseTypeDecl.entity,
                      dynamic_vars=[default_origin()], public=True)
    def return_type():
        """
        Returns the return type of Self, if applicable (eg. if Self is a
        subprogram). Else, returns null.
        """
        return Entity.returns._.designated_type.then(
            lambda t: Entity.real_type(t)
        )

    xref_entry_point = Property(True)
    xref_equation = Property(Entity.returns.then(lambda r: r.sub_equation,
                                                 default_val=LogicTrue()))


@synthetic
class EnumSubpSpec(BaseSubpSpec):
    """
    Synthetic node for the abstract subprogram spec of an enum literal.

    NOTE: This has no existence in the ARM. While enum literals are functions
    semantically, they're not such syntactically.
    """
    enum_decl = Property(Self.parent.cast(T.EnumLiteralDecl).as_entity)

    name = Property(Entity.enum_decl.name.node)
    returns = Property(Entity.enum_decl.synth_type_expr)
    params = Property(No(T.ParamSpec.entity.array))


class SubpSpec(BaseSubpSpec):
    """
    Subprogram specification.
    """
    subp_kind = Field(type=T.SubpKind)
    subp_name = Field(type=T.DefiningName)
    subp_params = Field(type=T.Params)
    subp_returns = Field(type=T.TypeExpr)

    name = Property(Self.subp_name)
    params = Property(Entity.subp_params._.params.map(lambda p: p))

    returns = Property(Entity.subp_returns)


class EntryDecl(BasicDecl):
    """
    Entry declaration.
    """
    overriding = Field(type=Overriding)
    spec = Field(type=T.EntrySpec)
    aspects = Field(type=T.AspectSpec)

    defining_names = Property(Entity.spec.name.as_entity.singleton)

    env_spec = EnvSpec(
        add_to_env_kv(Entity.name_symbol, Self),
        add_env()
    )


class EntrySpec(BaseSubpSpec):
    """
    Entry specification.
    """
    entry_name = Field(type=T.DefiningName)
    family_type = Field(type=T.AdaNode)
    entry_params = Field(type=T.Params)

    name = Property(Self.entry_name)
    params = Property(
        Entity.entry_params.then(
            lambda p: p.params.map(lambda p: p),
            default_val=No(T.ParamSpec.entity.array)
        )
    )
    returns = Property(No(T.TypeExpr.entity))


class Quantifier(AdaNode):
    """
    Type for quantified expressions.
    """
    enum_node = True
    alternatives = ["all", "some"]


class IterType(AdaNode):
    """
    Iteration type for ``for`` loops.
    """
    enum_node = True
    alternatives = ["in", "of"]


@abstract
class LoopSpec(AdaNode):
    """
    Base class for loop specifications.
    """
    pass


class ForLoopVarDecl(BasicDecl):
    """
    Declaration for the controlling variable in a ``for`` loop.
    """

    id = Field(type=T.DefiningName)
    id_type = Field(type=T.SubtypeIndication)
    aspects = NullField()

    defining_names = Property(Entity.id.singleton)

    defining_env = Property(Entity.expr_type.defining_env)

    @langkit_property(memoized=True, call_memoizable=True)
    def expr_type():
        return If(
            Self.id_type.is_null,

            # The type of a for loop variable does not need to be annotated, it
            # can eventually be infered, which necessitates name resolution on
            # the loop specification. Run resolution if necessary.
            Entity.id.expression_type,

            # If there is a type annotation, just return it
            Entity.id_type.designated_type
        )

    env_spec = EnvSpec(add_to_env_kv(Self.name_symbol, Self))


class ForLoopSpec(LoopSpec):
    """
    Specification for a ``for`` loop.
    """

    var_decl = Field(type=T.ForLoopVarDecl)
    loop_type = Field(type=IterType)
    has_reverse = Field(type=Reverse)
    iter_expr = Field(type=T.AdaNode)

    @langkit_property(memoized=True, call_memoizable=True)
    def iter_type():
        p = Var(Entity.iter_expr.resolve_names)
        typ = Var(If(p,
                     Entity.iter_expr.cast_or_raise(T.Expr)
                     .type_var.get_value.cast(T.BaseTypeDecl),
                     No(BaseTypeDecl.entity)))

        return bind_origin(Self, If(
            typ.is_implicit_deref,
            typ.accessed_type,
            typ
        ))

    @langkit_property(return_type=Equation)
    def xref_equation():
        return Self.loop_type.match(

            # This is a for .. in
            lambda _=IterType.alt_in:

            # Let's handle the different possibilities
            Entity.iter_expr.match(
                # Anonymous range case: for I in 1 .. 100
                lambda binop=T.BinOp:
                binop.sub_equation
                # The default type, if there is no other determined type, is
                # Integer.
                & Or(TypeBind(binop.type_var, Self.int_type), LogicTrue())
                & TypeBind(Self.var_decl.id.type_var, binop.type_var),

                # Subtype indication case: the induction variable is of the
                # type.
                lambda t=T.SubtypeIndication:
                t.sub_equation
                & TypeBind(Self.var_decl.id.type_var,
                           t.designated_type.canonical_type),

                lambda r=T.AttributeRef:
                r.sub_equation
                & TypeBind(Self.var_decl.id.type_var, r.type_var),

                # Name case: Either the name is a subtype indication, or an
                # attribute on a subtype indication, in which case the logic is
                # the same as above, either it's an expression that yields an
                # iterator.
                lambda t=T.Name: t.name_designated_type.then(
                    lambda typ:
                    t.sub_equation
                    & TypeBind(Self.var_decl.id.type_var, typ.canonical_type),

                    default_val=Entity.iterator_xref_equation
                ),

                lambda _: LogicTrue()  # should never happen
            ),

            # This is a for .. of
            lambda _=IterType.alt_of: Let(lambda it_typ=Entity.iter_type: If(

                it_typ.is_iterable_type,

                # Equation for the expression
                Entity.iter_expr.sub_equation

                # Then we want the type of the induction variable to be the
                # component type of the type of the expression.
                & TypeBind(Self.var_decl.id.type_var,
                           it_typ.iterable_comp_type)

                # If there is a type annotation, then the type of var should be
                # conformant.
                & If(Self.var_decl.id_type.is_null,
                     LogicTrue(),
                     TypeBind(Self.var_decl.id.type_var,
                              Entity.var_decl.id_type.designated_type)),

                LogicFalse()
            ))
        )

    @langkit_property(return_type=Equation, dynamic_vars=[env, origin])
    def iterator_xref_equation():
        iter_expr = Var(Entity.iter_expr.cast_or_raise(T.Expr))

        p = Var(iter_expr.resolve_names_internal(
            True,
            Predicate(BaseTypeDecl.is_iterator_type,
                      iter_expr.type_var)
        ))

        return If(
            p,
            TypeBind(
                Self.var_decl.id.type_var,
                iter_expr.type_var.get_value
                .children_env.get_first('Cursor').cast_or_raise(T.BaseTypeDecl)
            ),
            LogicFalse()
        )

    xref_entry_point = Property(True)


class QuantifiedExpr(Expr):
    """
    Quantified expression.
    """
    quantifier = Field(type=Quantifier)
    loop_spec = Field(type=T.ForLoopSpec)
    expr = Field(type=T.Expr)

    @langkit_property(return_type=Equation)
    def xref_equation():
        spec_success = Var(Entity.loop_spec.resolve_names)

        return If(
            spec_success,
            Entity.expr.sub_equation
            & bool_bind(Entity.expr.type_var),
            LogicFalse()
        )


class Allocator(Expr):
    """
    Allocator expression (``new ...``).
    """

    subpool = Field(type=T.Name)
    type_or_expr = Field(type=T.AdaNode)

    @langkit_property(public=True)
    def get_allocated_type():
        """
        Return the allocated type for this allocator.
        """
        return bind_origin(Self, Entity.type_or_expr.match(
            lambda t=SubtypeIndication.entity: t.designated_type,
            lambda q=QualExpr.entity: q.designated_type,
            lambda _: No(BaseTypeDecl.entity)
        ))

    @langkit_property(return_type=Equation)
    def xref_equation():
        return (
            Entity.type_or_expr.sub_equation
            & Predicate(BaseTypeDecl.matching_allocator_type,
                        Self.type_var, Entity.get_allocated_type)
        )


class QualExpr(Name):
    """
    Qualified expression (``...'(...)``).
    """

    prefix = Field(type=T.Name)
    suffix = Field(type=T.Expr)

    ref_var = Property(Self.prefix.ref_var)

    relative_name = Property(Entity.prefix.relative_name)

    @langkit_property(return_type=Equation, dynamic_vars=[env, origin])
    def general_xref_equation(root=(T.Name, No(T.Name))):
        return And(
            Entity.xref_equation,
            Self.parent_name(root).as_entity.then(
                lambda pn:
                pn.parent_name_equation(Entity.name_designated_type, root),
                default_val=LogicTrue()
            )
        )

    @langkit_property(return_type=Equation)
    def xref_equation():
        typ = Entity.prefix.designated_type_impl

        return (
            Entity.suffix.sub_equation
            & Bind(Self.prefix.ref_var, typ)
            & TypeBind(Self.prefix.type_var, typ)
            & TypeBind(Self.suffix.type_var, typ)
            & TypeBind(Self.type_var, typ)
        )

    # TODO: once we manage to turn prefix into a subtype indication, remove
    # this property and update Allocator.get_allocated type to do:
    # q.prefix.designated_type.
    designated_type = Property(
        env.bind(Entity.node_env,
                 bind_origin(Self, Entity.designated_type_impl)),
    )
    designated_type_impl = Property(Entity.prefix.designated_type_impl)

    @langkit_property()
    def designated_env():
        return Entity.designated_type.defining_env


class AttributeRef(Name):
    """
    Expression to reference an attribute.
    """

    prefix = Field(type=T.Name)
    attribute = Field(type=T.Identifier)
    args = Field(type=T.AdaNode)

    ref_var = Property(Self.r_ref_var)
    r_ref_var = UserField(type=LogicVar, public=False)

    relative_name = Property(Entity.prefix.relative_name)

    designated_type_impl = Property(Cond(
        Self.attribute.sym == 'Class',
        Entity.prefix.designated_type_impl._.classwide_type,

        Self.attribute.sym == 'Base',
        Entity.prefix.name_designated_type.scalar_base_subtype,

        Entity.prefix.designated_type_impl
    ))

    args_list = Property(Entity.args._.cast_or_raise(T.AssocList))

    @langkit_property()
    def env_elements_impl():
        return If(
            Self.attribute.sym == 'Unrestricted_Access',
            Entity.prefix.env_elements_impl.map(
                lambda e:
                # Using unrestricted accesses, the entities are actually
                # anonymous access to entities, so mark the entities as such.
                e.cast_or_raise(T.BasicDecl).trigger_access_entity(True)
            ),
            No(T.AdaNode.entity.array),
        )

    is_access_attr = Property(
        Entity.attribute.name_symbol.any_of(
            'Access', 'Unchecked_Access', 'Unrestricted_Access'
        )
    )

    @langkit_property()
    def designated_env():
        return Cond(
            Entity.attribute.name_is('Model'),
            Entity.designated_env_model_attr,

            Entity.is_access_attr,
            Entity.prefix.designated_env,

            Entity.attribute.name_is('Result'),
            Self.parents.find(lambda p: p.is_a(BasicSubpDecl, SubpBody))
            .as_entity.cast(T.BasicDecl).subp_spec_or_null
            .return_type.defining_env,

            EmptyEnv
        )

    @langkit_property(return_type=LexicalEnv, dynamic_vars=[env, origin])
    def designated_env_model_attr():
        model_types = Var(
            Entity.prefix.env_elements
            .map(lambda e: e.cast_or_raise(T.BasicDecl).expr_type)
            .map(lambda t: t.modeled_type(Self.unit))
            .filter(lambda t: Not(t.is_null))
        )

        return model_types.map(lambda mt: mt.defining_env).env_group()

    @langkit_property()
    def xref_equation():
        rel_name = Var(Entity.attribute.name_symbol)
        return Cond(
            rel_name.any_of('Succ', 'Pred'), Entity.succpred_xref_equation,
            rel_name.any_of('Min', 'Max'), Entity.minmax_equation,

            rel_name.any_of('First', 'Last', 'Range', 'Length'),
            Entity.array_attr_equation,

            rel_name == 'Size', Entity.size_equation,
            rel_name == 'Pos', Entity.pos_equation,
            rel_name == 'Val', Entity.val_equation,

            rel_name.any_of('Max_Size_In_Storage_Elements',
                            'Object_Size', 'Value_Size', 'Aft'),
            Entity.subtype_attr_equation,

            rel_name.any_of('Access',
                            'Unchecked_Access', 'Unrestricted_Access'),
            Entity.access_equation,

            rel_name == 'Image',
            Entity.image_equation(Self.std_entity('String')),

            rel_name == 'Wide_Image',
            Entity.image_equation(Self.std_entity('Wide_String')),

            rel_name == 'Wide_Wide_Image',
            Entity.image_equation(Self.std_entity('Wide_Wide_String')),

            rel_name == 'Value',
            Entity.value_equation(Self.std_entity('String')),

            rel_name == 'Wide_Value',
            Entity.value_equation(Self.std_entity('Wide_String')),

            rel_name == 'Wide_Wide_Value',
            Entity.value_equation(Self.std_entity('Wide_Wide_String')),

            rel_name == 'Identity', Entity.identity_equation,
            rel_name == 'Address', Entity.address_equation,

            rel_name.any_of('Maximum_Alignment', 'Word_Size'),
            Entity.standard_attr_equation,

            rel_name.any_of('Small', 'Large'),
            Entity.universal_real_equation,

            rel_name == 'Img',
            Entity.img_equation(Self.std_entity('String')),

            rel_name.any_of('Write', 'Read', 'Output'),
            Entity.stream_attrs_equation(False),

            rel_name == 'Input', Entity.stream_attrs_equation(True),

            rel_name == 'Tag', Entity.tag_attr_equation,

            rel_name == 'Result', Entity.result_attr_equation,
            rel_name == 'Old',    Entity.old_attr_equation,

            rel_name == 'Class',  Entity.prefix.sub_equation,

            rel_name == 'Valid',
            Entity.prefix.sub_equation
            & bool_bind(Self.type_var),

            # Lal checkers specific
            rel_name == 'Model', Entity.model_attr_equation,

            LogicTrue()
        )

    @langkit_property(return_type=Equation, dynamic_vars=[env, origin])
    def model_attr_equation():
        return (
            Entity.prefix.sub_equation
            & Self.type_var.domain(
                Self.top_level_decl(Self.unit)
                .cast_or_raise(T.PackageDecl).public_part
                .types_with_models.map(lambda t: t.cast(T.AdaNode))
            )
            & TypeBind(Self.type_var, Self.prefix.type_var,
                       conv_prop=BaseTypeDecl.model_of_type)
        )

    @langkit_property(return_type=Equation, dynamic_vars=[env, origin])
    def old_attr_equation():
        return And(
            Entity.prefix.sub_equation,
            TypeBind(Self.type_var, Self.prefix.type_var),
        )

    @langkit_property(return_type=Equation, dynamic_vars=[env, origin])
    def result_attr_equation():
        containing_subp = Var(Self.parents.find(
            lambda p: p.is_a(BasicSubpDecl, BaseSubpBody)
        ).as_entity.cast(T.BasicDecl))

        returns = Var(containing_subp.subp_spec_or_null.then(
            lambda ss: ss.return_type
        ))

        return And(
            TypeBind(Self.type_var, returns),
            Bind(Entity.prefix.ref_var, containing_subp)
        )

    @langkit_property(return_type=Equation, dynamic_vars=[env, origin])
    def tag_attr_equation():
        tag_type = Var(
            Entity
            .get_unit_root_decl(['Ada', 'Tags'], UnitSpecification)
            ._.children_env.get_first('Tag', lookup=LK.flat)
            .cast(T.BaseTypeDecl)
        )

        return (
            # Prefix is an expression, bind prefix's ref var to it
            Entity.prefix.xref_equation

            # Type of self is String
            & TypeBind(Self.type_var, tag_type)
        )

    @langkit_property(return_type=Equation, dynamic_vars=[env, origin])
    def stream_attrs_equation(return_obj=(Bool, False)):
        typ = Var(Entity.prefix.name_designated_type)

        root_stream_type = Var(
            Entity
            .get_unit_root_decl(['Ada', 'Streams'], UnitSpecification)
            ._.children_env.get_first('Root_Stream_Type', lookup=LK.flat)
            .cast(T.BaseTypeDecl).classwide_type.cast(T.BaseTypeDecl)
        )

        stream_arg = Var(Entity.args_list.at(0).expr)
        obj_arg = Var(Entity.args_list.at(1)._.expr)

        return (
            Entity.prefix.sub_equation
            & stream_arg.sub_equation
            & TypeBind(stream_arg.type_var,
                       root_stream_type.anonymous_access_type)
            & If(
                return_obj,
                TypeBind(Self.type_var, typ),
                TypeBind(obj_arg.type_var, typ)
                & obj_arg.sub_equation
            )
        )

    @langkit_property(return_type=Equation, dynamic_vars=[env, origin])
    def address_equation():
        address_type = Var(
            Entity
            .get_unit_root_decl(['System'], UnitSpecification)
            ._.children_env.get_first('Address', lookup=LK.flat)
            .cast(T.BaseTypeDecl)
        )
        return (Or(Entity.prefix.sub_equation,
                   # In case the 'Address is pointing to a subprogram, we use
                   # xref_no_overloading in order to not filter eagerly on
                   # parameters.
                   Entity.prefix.xref_no_overloading)
                & TypeBind(Self.type_var, address_type))

    @langkit_property(return_type=Equation, dynamic_vars=[env, origin])
    def identity_equation():
        # NOTE: We don't verify that the prefix designates an exception
        # declaration, because that's legality, not name resolution.
        return (Entity.prefix.sub_equation
                & TypeBind(Self.prefix.ref_var, Self.type_var,
                           conv_prop=BasicDecl.identity_type))

    @langkit_property(return_type=Equation, dynamic_vars=[env, origin])
    def universal_real_equation():
        return (
            universal_real_bind(Self.type_var)
            & Entity.prefix.sub_equation
        )

    @langkit_property(return_type=Equation, dynamic_vars=[env, origin])
    def standard_attr_equation():
        return (
            # TODO: run the equation of the prefix (std package), does not
            # work for the moment because the architecture is wrong.
            universal_int_bind(Self.type_var)
        )

    @langkit_property(return_type=Equation, dynamic_vars=[env, origin])
    def succpred_xref_equation():
        typ = Var(Entity.prefix.name_designated_type)
        arg = Var(Entity.args_list.at(0).expr)

        return (
            TypeBind(Self.prefix.ref_var, typ)
            & TypeBind(arg.type_var, typ)
            & TypeBind(Self.type_var, typ)
            & arg.sub_equation
        )

    @langkit_property(return_type=Equation, dynamic_vars=[env, origin])
    def minmax_equation():
        typ = Var(Entity.prefix.name_designated_type)
        left = Var(Entity.args_list.at(0).expr)
        right = Var(Entity.args_list.at(1).expr)

        return (
            left.sub_equation & right.sub_equation
            # Prefix is a type, bind prefix's ref var to it
            & TypeBind(Self.prefix.ref_var, typ)
            & TypeBind(left.type_var, right.type_var)
            & TypeBind(Self.type_var, left.type_var)
            & TypeBind(Self.type_var, typ)
        )

    @langkit_property(return_type=Equation, dynamic_vars=[env, origin])
    def value_equation(str_type=T.AdaNode.entity):
        typ = Var(Entity.prefix.name_designated_type)
        expr = Var(Entity.args_list.at(0).expr)

        return (
            expr.sub_equation

            # Prefix is a type, bind prefix's ref var to it
            & Bind(Self.prefix.ref_var, typ)

            # Type of expression is str_type
            & TypeBind(expr.type_var, str_type)

            # Type of self is designated type
            & TypeBind(Self.type_var, typ)
        )

    @langkit_property(return_type=Equation, dynamic_vars=[env, origin])
    def image_equation(str_type=T.AdaNode.entity):
        typ = Var(Entity.prefix.name_designated_type)
        expr = Var(Entity.args_list.then(lambda al: al.at(0).expr))

        return If(
            typ.is_null,

            # If prefix is not a type, then it is an expression
            Entity.prefix.sub_equation
            & TypeBind(Self.type_var, str_type),

            expr.sub_equation
            # Prefix is a type, bind prefix's ref var to it
            & Bind(Self.prefix.ref_var, typ)
            # Type of expression is designated type
            & TypeBind(expr.type_var, typ)
            # Type of self is String
            & TypeBind(Self.type_var, str_type)
        )

    @langkit_property(return_type=Equation, dynamic_vars=[env, origin])
    def img_equation(str_type=T.AdaNode.entity):
        return (
            # Prefix is an expression, bind prefix's ref var to it
            Entity.prefix.xref_equation

            # Type of self is String
            & TypeBind(Self.type_var, str_type)
        )

    @langkit_property(return_type=Equation, dynamic_vars=[env, origin])
    def pos_equation():
        typ = Var(Entity.prefix.name_designated_type)
        expr = Var(Entity.args_list.at(0).expr)

        return (
            # Prefix is a type, bind prefix's ref var to it
            Bind(Self.prefix.ref_var, typ)
            & universal_int_bind(Self.type_var)
            & Bind(expr.type_var, typ)
            & expr.sub_equation
        )

    @langkit_property(return_type=Equation, dynamic_vars=[env, origin])
    def val_equation():
        typ = Var(Entity.prefix.name_designated_type)
        expr = Var(Entity.args_list.at(0).expr)
        return (
            # Prefix is a type, bind prefix's ref var to it
            Bind(Self.prefix.ref_var, typ)
            & TypeBind(Self.type_var, typ)
            & universal_int_bind(expr.type_var)
            & expr.sub_equation
        )

    @langkit_property(return_type=Equation, dynamic_vars=[env, origin])
    def access_equation():
        return Or(
            # Access to subprogram
            Entity.prefix.xref_no_overloading(all_els=True)
            & Predicate(BaseTypeDecl.is_subp_access_of,
                        Self.type_var,
                        Self.prefix.ref_var),

            Entity.prefix.xref_equation
            & If(
                Entity.attribute.name_is('Unrestricted_Access'),
                Bind(Self.prefix.type_var,
                     Self.type_var,
                     conv_prop=BaseTypeDecl.anonymous_access_type_or_null,
                     eq_prop=BaseTypeDecl.matching_prefix_type),

                Bind(Self.type_var,
                     Self.prefix.type_var,
                     conv_prop=BaseTypeDecl.accessed_type,
                     eq_prop=BaseTypeDecl.matching_formal_type_inverted),
            )
        )

    @langkit_property(return_type=Equation, dynamic_vars=[env, origin])
    def size_equation():
        typ = Var(Entity.prefix.name_designated_type)
        return If(
            Not(typ.is_null),

            Bind(Self.prefix.ref_var, typ)
            & universal_int_bind(Self.type_var),

            Entity.prefix.sub_equation
            & universal_int_bind(Self.type_var)
        )

    @langkit_property(return_type=Equation, dynamic_vars=[env, origin])
    def array_attr_equation():
        is_length = Var(Entity.attribute.name_is('Length'))
        typ = Var(Entity.prefix.name_designated_type)

        # If the range attribute has an argument, then it's a static expression
        # representing an int that we will use as a dimension.
        dim = Var(Entity.args_list.then(lambda a: a.at(0).expr.then(
            lambda expr: Let(
                lambda _=expr.resolve_names_internal(
                    True, LogicTrue()
                ):
                expr.eval_as_int.as_int
            ), default_val=1), default_val=1
        ) - 1)

        return If(
            Not(typ.is_null),

            # Prefix is a type
            Bind(Self.prefix.ref_var, typ) & Cond(
                typ.is_array & is_length, universal_int_bind(Self.type_var),

                # If it's an array, take the appropriate index type
                typ.is_array, TypeBind(Self.type_var, typ.index_type(dim)),

                # If it's a discrete type, then bind to the discrete type
                typ.is_discrete_type | typ.is_real_type & Not(is_length),

                TypeBind(Self.type_var, typ),

                LogicFalse()
            ),

            # Prefix is not a type: In that case we have permission to resolve
            # prefix separately.
            Let(lambda
                res=Entity.prefix.resolve_names_internal(
                    True,
                    Predicate(BaseTypeDecl.is_array_def_with_deref,
                              Entity.prefix.type_var)
                ),
                pfx_typ=Entity.prefix.type_val.cast(T.BaseTypeDecl):

                If(res,
                   If(is_length,
                      universal_int_bind(Self.type_var),
                      TypeBind(Self.type_var, pfx_typ.index_type(dim)))
                   & Entity.prefix.xref_equation
                   & Predicate(BaseTypeDecl.is_array_def_with_deref,
                               Entity.prefix.type_var),
                   LogicFalse()))
        )

    @langkit_property(return_type=Equation, dynamic_vars=[env, origin])
    def subtype_attr_equation():
        """
        Generates the xref equation for a an attribute that is defined on any
        subtype and that evaluates to an universal integer.
        """
        return (
            Bind(Self.prefix.ref_var, Entity.prefix.name_designated_type) &
            universal_int_bind(Self.type_var)
        )


class UpdateAttributeRef(AttributeRef):
    """
    Reference to the ``Update`` attribute.
    """
    pass


class RaiseExpr(Expr):
    """
    Expression to raise an exception.
    """

    exception_name = Field(type=T.Name)
    error_message = Field(type=T.Expr)

    @langkit_property()
    def xref_equation():
        return Entity.exception_name.sub_equation


class DottedName(Name):
    """
    Name to select a suffix in a prefix.
    """

    prefix = Field(type=T.Name)
    suffix = Field(type=T.BaseId)
    ref_var = Property(Self.suffix.ref_var)

    subp_spec_var = Property(Self.suffix.subp_spec_var)
    defines_subp_spec_var = Property(True)

    @langkit_property(return_type=T.BasicDecl.entity.array)
    def complete():
        return bind_origin(Self, env.bind(
            Self.node_env,
            Entity.prefix.designated_env.get(No(Symbol), LK.flat).map(
                lambda n: n.cast(T.BasicDecl)
            )
        ))

    @langkit_property()
    def designated_env_no_overloading():
        pfx_env = Var(Entity.prefix.designated_env_no_overloading)
        return env.bind(pfx_env,
                        Entity.suffix.designated_env_no_overloading)

    @langkit_property()
    def designated_env():
        pfx_env = Var(Entity.prefix.designated_env)
        return env.bind(pfx_env, Entity.suffix.designated_env)

    @langkit_property()
    def all_env_els_impl(seq=(Bool, True),
                         seq_from=(AdaNode, No(T.AdaNode))):
        pfx_env = Var(Entity.prefix.designated_env)
        return env.bind(pfx_env, Entity.suffix.all_env_els_impl(seq, seq_from))

    scope = Property(Self.suffix.then(
        lambda sfx: env.bind(Self.parent_scope, sfx.scope),
        default_val=EmptyEnv
    ))

    parent_scope = Property(Self.prefix.scope)

    relative_name = Property(Entity.suffix.relative_name)
    base_name = Property(Entity.prefix)

    @langkit_property()
    def env_elements_impl():
        pfx_env = Var(bind_origin(Self, Entity.prefix.designated_env))
        return env.bind(pfx_env, Entity.suffix.env_elements_baseid)

    @langkit_property()
    def designated_type_impl():
        return env.bind(Entity.prefix.designated_env_no_overloading,
                        Entity.suffix.designated_type_impl)

    @langkit_property()
    def xref_equation():
        base = Var(Entity.prefix.sub_equation
                   & env.bind(Entity.prefix.designated_env,
                              Entity.suffix.sub_equation))

        return If(
            Not(Entity.designated_type_impl.is_null),
            base,
            base & Entity.env_elements.logic_any(lambda e: (
                Bind(Self.suffix.ref_var, e)
                & e.cast(BasicDecl.entity).constrain_prefix(Self.prefix)
                & TypeBind(Self.type_var, Self.suffix.type_var)
            ))
        )


class CompilationUnit(AdaNode):
    """
    Root node for all Ada analysis units.
    """

    prelude = Field(doc="``with``, ``use`` or ``pragma`` statements.")
    body = Field(type=T.AdaNode)
    pragmas = Field(type=T.Pragma.list)
    no_env = UserField(type=T.LexicalEnv, public=False)

    @langkit_property(external=True, uses_entity_info=False, uses_envs=True,
                      return_type=LexicalEnv)
    def get_empty_env():
        """
        Returns an empty env to use in env specs. This is meant as an
        optimization: Langkit referenced envs that return empty env can never
        be cached, so we used a CompilationUnit specific empty env, that will
        live for the same duration as its analysis unit, and then be
        invalidated.
        """
        pass

    @langkit_property(public=True)
    def syntactic_fully_qualified_name():
        """
        Return the syntactic fully qualified name of this compilation unit.
        """
        return Self.as_bare_entity.body.match(
            lambda li=T.LibraryItem: li.item.defining_name.as_symbol_array,
            lambda su=T.Subunit: su.name.as_symbol_array.concat(
                su.body.defining_name.as_symbol_array
            ),
            lambda _: PropertyError(
                Symbol.array, 'Unexpected CompilationUnit.f_body attribute'
            ),
        )

    @langkit_property(public=True)
    def unit_kind():
        """
        Return the kind corresponding to this analysis unit.
        """
        return Self.body.match(
            lambda li=T.LibraryItem: li.item.match(
                lambda _=T.Body: UnitBody,
                lambda _: UnitSpecification
            ),
            lambda _=T.Subunit: UnitBody,
            lambda _: PropertyError(
                AnalysisUnitKind, 'Unexpected CompilationUnit.f_body attribute'
            ),
        )

    env_spec = EnvSpec(
        set_initial_env(Let(
            lambda n=Self.body.cast(T.LibraryItem).then(
                lambda i: i.item.as_bare_entity.defining_name
            ):

            Cond(
                Self.body.is_a(T.Subunit), Self.std_env,

                n.is_null, Self.initial_env,

                # If self is Standard package, then register self in the root
                # env.
                n.name.is_a(T.BaseId) & (n.name_is('Standard')),
                Self.initial_env,

                Self.std_env
            )
        ))
    )


@abstract
class BaseSubpBody(Body):
    """
    Base class for subprogram bodies.
    """

    env_spec = EnvSpec(
        call_env_hook(Self),

        set_initial_env(
            env.bind(Self.initial_env, Entity.body_scope(False)),
        ),

        # Add the body to its own parent env
        add_to_env_kv(Entity.name_symbol, Self,
                      dest_env=env.bind(Self.initial_env,
                                        Entity.body_scope(False))),

        add_env(transitive_parent=True),
        populate_dependent_units(),
        ref_used_packages(),

        # If Self, which is assumed to be a SubpBody, is a library-level
        # subprogram, it must "inherit" the use clauses of its declaration, if
        # there is one.
        reference(
            Self.cast(T.AdaNode)._.singleton,
            through=T.AdaNode.use_packages_in_spec_of_subp_body,
            cond=Self.parent.is_a(T.LibraryItem, T.Subunit)
        ),

        ref_generic_formals(),

        handle_children(),

        # Adding subp to the type's environment if the type is tagged and self
        # is a primitive of it.
        add_to_env(
            Self.as_bare_entity.subp_spec.dottable_subp_of.map(
                lambda t: new_env_assoc(
                    key=Entity.name_symbol, val=Self,
                    dest_env=t.children_env,
                    # We pass custom metadata, marking the entity as a dottable
                    # subprogram.
                    metadata=new_metadata(dottable_subp=True)
                )
            ),
        ),

        # Adding subp to the primitives env if the subp is a primitive
        add_to_env(
            Self.as_bare_entity.subp_spec.primitive_subp_of.filtermap(
                lambda t: new_env_assoc(
                    key=Entity.name_symbol, val=Self,
                    dest_env=t.cast_or_raise(T.TypeDecl).primitives,
                    metadata=new_metadata(primitive=t.node)
                ),
                lambda t: t.is_a(T.TypeDecl)
            )
        )
    )

    overriding = Field(type=Overriding)
    subp_spec = Field(type=T.SubpSpec)

    defining_names = Property(Entity.subp_spec.name.as_entity.singleton)

    @langkit_property(return_type=LexicalEnv, dynamic_vars=[origin])
    def defining_env():
        return If(
            Entity.in_scope,

            If(
                Entity.subp_spec_or_null
                ._.paramless(Entity.info.md.dottable_subp, can_be=True),
                Array([
                    Entity.children_env, Entity.subp_spec.defining_env
                ]).env_group(),
                Entity.children_env
            ),

            Entity.subp_spec.defining_env
        )

    type_expression = Property(Entity.subp_spec.returns)

    @langkit_property()
    def expr_type():
        return Entity.subp_spec_or_null._.return_type


class ExprFunction(BaseSubpBody):
    """
    Expression function.
    """

    expr = Field(type=T.Expr)
    aspects = Field(type=T.AspectSpec)

    xref_equation = Property(
        Entity.expr.sub_equation
        & Bind(Entity.expr.type_var, Entity.subp_spec.return_type,
               eq_prop=BaseTypeDecl.matching_assign_type)
    )

    xref_entry_point = Property(True)


class NullSubpDecl(BaseSubpBody):
    """
    Declaration for a null subprogram.
    """

    aspects = Field(type=T.AspectSpec)


class SubpRenamingDecl(BaseSubpBody):
    """
    Declaration for a subprogram renaming.
    """

    renames = Field(type=T.RenamingClause)
    aspects = Field(type=T.AspectSpec)

    xref_entry_point = Property(True)
    xref_equation = Property(Or(
        And(
            Entity.renames.renamed_object.xref_no_overloading(all_els=True),
            Predicate(BasicDecl.subp_decl_match_signature,
                      Entity.renames.renamed_object.ref_var,
                      Entity.cast(T.BasicDecl))
        ),
        # Operators might be built-in, so if we cannot find a reference, we'll
        # just abandon resolution...
        If(Entity.renames.renamed_object.is_operator_name,
           LogicTrue(), LogicFalse())
    ))


class SubpBody(BaseSubpBody):
    """
    Subprogram body.
    """

    aspects = Field(type=T.AspectSpec)
    decls = Field(type=T.DeclarativePart)
    stmts = Field(type=T.HandledStmts)
    end_name = Field(type=T.EndName)

    declarative_region = Property(Entity.decls)


class HandledStmts(AdaNode):
    """
    List of statements, with optional exception handlers.
    """

    annotations = Annotations(snaps=True)

    stmts = Field(type=T.StmtList)
    exceptions = Field(type=T.AdaNode.list)


class ExceptionHandler(BasicDecl):
    """
    Exception handler.
    """

    exception_name = Field(type=T.DefiningName)
    handled_exceptions = Field(type=T.AlternativesList)
    stmts = Field(type=T.StmtList)
    aspects = NullField()

    env_spec = EnvSpec(
        add_env(),
        add_to_env(
            env_mappings(Entity.exception_name.then(lambda n: n.singleton),
                         Self, dest_env=Self.children_env),
        )
    )

    defining_names = Property(Entity.exception_name.singleton)

    @langkit_property()
    def expr_type():
        return (
            Entity
            .get_unit_root_decl(['Ada', 'Exceptions'], UnitSpecification)
            ._.children_env.get_first('Exception_Occurrence', lookup=LK.flat)
            .cast(T.BaseTypeDecl)
        )

    xref_equation = Property(
        Self.handled_exceptions.logic_all(lambda he: he.as_entity.sub_equation)
    )

    xref_entry_point = Property(True)


@abstract
class Stmt(AdaNode):
    """
    Bass class for statements.
    """

    xref_entry_point = Property(True)


class ErrorStmt(Stmt):
    """
    Placeholder node for syntax errors in lists of statements.
    """

    pass


@abstract
class SimpleStmt(Stmt):
    """
    Base class for simple statements.
    """

    pass


@abstract
class CompositeStmt(Stmt):
    """
    Base class for composite statements.
    """

    pass


class CallStmt(SimpleStmt):
    """
    Statement for entry or procedure calls.
    """

    call = Field(type=T.Name)

    @langkit_property()
    def xref_equation():
        return (
            Entity.call.sub_equation

            # Call statements can have no return value
            & Bind(Self.call.type_var, No(AdaNode.entity))
        )


class NullStmt(SimpleStmt):
    """
    ``null;`` statement.
    """

    @langkit_property()
    def xref_equation():
        return LogicTrue()


class AssignStmt(SimpleStmt):
    """
    Statement for assignments.
    """

    dest = Field(type=T.Name)
    expr = Field(type=T.Expr)

    @langkit_property()
    def xref_equation():
        return (
            Entity.dest.sub_equation
            & Entity.expr.sub_equation
            & Bind(Self.expr.type_var, Self.dest.type_var,
                   eq_prop=BaseTypeDecl.matching_assign_type)
        )


class GotoStmt(SimpleStmt):
    """
    ``goto`` statement.
    """

    label_name = Field(type=T.Name)

    @langkit_property()
    def xref_equation():
        return Entity.label_name.xref_no_overloading(sequential=False)


class ExitStmt(SimpleStmt):
    """
    ``exit`` statement.
    """

    loop_name = Field(type=T.Identifier)
    cond_expr = Field(type=T.Expr)

    @langkit_property()
    def xref_equation():
        return And(
            Entity.cond_expr.then(lambda cond: (
                cond.sub_equation
                & bool_bind(cond.type_var)
            ), default_val=LogicTrue()),

            Entity.loop_name.then(
                lambda ln: ln.xref_no_overloading,
                default_val=LogicTrue()
            )
        )


class ReturnStmt(SimpleStmt):
    """
    ``return`` statement.
    """

    return_expr = Field(type=T.Expr)

    subp = Property(
        Self.parents.find(lambda p: p.is_a(SubpBody)).cast(SubpBody).as_entity,
        doc="Returns the subprogram this return statement belongs to"
    )

    @langkit_property()
    def xref_equation():
        return Entity.return_expr.then(
            lambda rexpr:
            rexpr.sub_equation
            & Bind(
                rexpr.type_var,
                Entity.subp.subp_spec.returns.designated_type,
                eq_prop=BaseTypeDecl.matching_assign_type
            ),
            default_val=LogicTrue()
        )


class RequeueStmt(SimpleStmt):
    """
    ``requeue`` statement.
    """

    call_name = Field(type=T.Expr)
    has_abort = Field(type=Abort)


class AbortStmt(SimpleStmt):
    """
    ``abort`` statement.
    """

    names = Field(type=T.Name.list)

    @langkit_property()
    def xref_equation():
        return Entity.names.logic_all(
            lambda name:
            name.sub_equation & Predicate(BaseTypeDecl.is_task_type,
                                          name.type_var)
        )


class DelayStmt(SimpleStmt):
    """
    ``delay`` statement.
    """

    has_until = Field(type=Until)
    expr = Field(type=T.Expr)

    @langkit_property()
    def xref_equation():
        return Entity.expr.sub_equation & If(
            Self.has_until.as_bool, LogicTrue(),
            TypeBind(Self.expr.type_var, Self.std_entity('Duration'))
        )


class RaiseStmt(SimpleStmt):
    """
    ``raise`` statement.
    """

    exception_name = Field(type=T.Name)
    error_message = Field(type=T.Expr)

    @langkit_property()
    def xref_equation():
        return And(
            Entity.exception_name.then(
                lambda en: en.sub_equation,
                default_val=LogicTrue()
            ),
            Entity.error_message.then(
                lambda er: And(
                    Predicate(BaseTypeDecl.is_str_type_or_null, er.type_var),
                    er.sub_equation
                ),
                default_val=LogicTrue()
            )
        )


class IfStmt(CompositeStmt):
    """
    ``if`` statement block.
    """

    cond_expr = Field(type=T.Expr)
    then_stmts = Field(type=T.StmtList)
    alternatives = Field(type=T.ElsifStmtPart.list)
    else_stmts = Field(type=T.StmtList)

    @langkit_property()
    def xref_equation():
        return (
            Entity.cond_expr.sub_equation
            & bool_bind(Self.cond_expr.type_var)
            & Entity.alternatives.logic_all(
                lambda elsif: elsif.cond_expr.sub_equation
                & bool_bind(elsif.cond_expr.type_var)
            )
        )


class ElsifStmtPart(AdaNode):
    """
    ``elsif`` part in an ``if`` statement block.
    """

    cond_expr = Field(type=T.Expr)
    stmts = Field(type=T.StmtList)


class LabelDecl(BasicDecl):
    """
    Declaration for a code label.
    """

    name = Field(type=T.DefiningName)
    aspects = NullField()

    defining_names = Property(Entity.name.singleton)

    env_spec = EnvSpec(add_to_env_kv(Self.name_symbol, Self))


class Label(SimpleStmt):
    """
    Statement to declare a code label.
    """

    decl = Field(type=T.LabelDecl)

    @langkit_property(return_type=Equation)
    def xref_equation():
        return LogicTrue()


class WhileLoopSpec(LoopSpec):
    """
    Specification for a ``while`` loop.
    """

    expr = Field(type=T.Expr)

    @langkit_property(return_type=Equation)
    def xref_equation():
        return Entity.expr.sub_equation & (
            bool_bind(Self.expr.type_var)
        )


class NamedStmtDecl(BasicDecl):
    """
    BasicDecl that is always the declaration inside a named statement.
    """
    name = Field(type=T.DefiningName)
    aspects = NullField()

    defining_names = Property(Entity.name.singleton)
    defining_env = Property(Self.parent.cast(T.NamedStmt).stmt.children_env)


class NamedStmt(CompositeStmt):
    """
    Wrapper class, used for composite statements that can be named (declare
    blocks, loops). This allows to both have a BasicDecl for the named entity
    declared, and a CompositeStmt for the statement hierarchy.
    """
    decl = Field(type=T.NamedStmtDecl)
    stmt = Field(type=T.CompositeStmt)

    env_spec = EnvSpec(
        add_to_env_kv(Self.decl.name_symbol, Self.decl),
        add_env()
    )

    xref_equation = Property(LogicTrue())


@abstract
class BaseLoopStmt(CompositeStmt):
    """
    Base class for loop statements.
    """

    spec = Field(type=T.LoopSpec)
    stmts = Field(type=T.StmtList)
    end_name = Field(type=T.EndName)

    @langkit_property(return_type=Equation)
    def xref_equation():
        return Entity.spec.then(lambda s: s.xref_equation,
                                default_val=LogicTrue())


class LoopStmt(BaseLoopStmt):
    """
    Statement for simple loops (``loop ... end loop;``).
    """

    pass


class ForLoopStmt(BaseLoopStmt):
    """
    Statement for ``for`` loops (``for ... loop ... end loop;``).
    """

    env_spec = EnvSpec(add_env())


class WhileLoopStmt(BaseLoopStmt):
    """
    Statement for ``while`` loops (``while ... loop ... end loop;``).
    """

    pass


@abstract
class BlockStmt(CompositeStmt):
    """
    Base class for statement blocks.
    """

    env_spec = EnvSpec(add_env())

    xref_equation = Property(LogicTrue())


class DeclBlock(BlockStmt):
    """
    Statement block with a declarative part.
    """

    decls = Field(type=T.DeclarativePart)
    stmts = Field(type=T.HandledStmts)
    end_name = Field(type=T.EndName)


class BeginBlock(BlockStmt):
    """
    Statement block with no declarative part.
    """

    stmts = Field(type=T.HandledStmts)
    end_name = Field(type=T.EndName)


class ExtendedReturnStmt(CompositeStmt):
    """
    Extended ``return`` statement.
    """

    decl = Field(type=T.ExtendedReturnStmtObjectDecl)
    stmts = Field(type=T.HandledStmts)

    @langkit_property(return_type=Equation)
    def xref_equation():
        return LogicTrue()

    env_spec = EnvSpec(add_env())


class CaseStmt(CompositeStmt):
    """
    ``case`` statement.
    """

    expr = Field(type=T.Expr)
    alternatives = Field(type=T.CaseStmtAlternative.list)

    @langkit_property()
    def xref_equation():
        ignore(Var(Entity.expr.resolve_names_internal(
            True, Predicate(BaseTypeDecl.is_discrete_type,
                            Self.expr.type_var)
        )))

        return Entity.alternatives.logic_all(lambda alt: (
            alt.choices.logic_all(lambda c: c.match(
                # Expression case
                lambda e=T.Expr:
                TypeBind(e.type_var, Self.expr.type_val) & e.sub_equation,

                # TODO: Bind other cases: SubtypeIndication and Range
                lambda _: LogicTrue()
            ))
        ))


class CaseStmtAlternative(AdaNode):
    """
    Alternative in a ``case`` statement (``when ... => ...``).
    """

    choices = Field(type=T.AlternativesList)
    stmts = Field(type=T.StmtList)


class AcceptStmt(CompositeStmt):
    """
    ``accept`` statement.
    """

    name = Field(type=T.Identifier)
    entry_index_expr = Field(type=T.Expr)
    params = Field(type=T.Params)

    env_spec = EnvSpec(add_env())

    xref_equation = Property(LogicTrue())


class AcceptStmtWithStmts(AcceptStmt):
    """
    Extended ``accept`` statement.
    """

    stmts = Field(type=T.HandledStmts)
    end_name = Field(type=T.EndName)

    xref_equation = Property(LogicTrue())


class SelectStmt(CompositeStmt):
    """
    ``select`` statements block.
    """

    guards = Field(type=T.SelectWhenPart.list)
    else_stmts = Field(type=T.StmtList)
    abort_stmts = Field(type=T.StmtList)

    @langkit_property()
    def xref_equation():
        return Entity.guards.logic_all(lambda wp: wp.sub_equation)


class SelectWhenPart(AdaNode):
    """
    Alternative part in a ``select`` statements block.
    """

    cond_expr = Field(type=T.Expr)
    stmts = Field(type=T.StmtList)

    @langkit_property()
    def xref_equation():
        return Entity.cond_expr.then(
            lambda c:
            c.sub_equation & bool_bind(Self.cond_expr.type_var),
            default_val=LogicTrue()
        )


class TerminateAlternative(SimpleStmt):
    """
    ``terminate`` alternative in a ``select`` statement.
    """

    xref_equation = Property(LogicTrue())


class PackageBody(Body):
    """
    Package body.
    """

    env_spec = child_unit(
        '__nextpart',

        # Parent link is the package's decl, or private part if there is one
        Entity.body_scope(follow_private=True),

        # Destination env for the __nextpart link
        dest_env=env.bind(
            Self.initial_env,
            If(
                Self.is_subunit,
                Entity.subunit_pkg_stub_env,

                # __nextpart never goes into the private part, and is always in
                # the decl for nested sub packages.
                Entity.body_scope(follow_private=False, force_decl=True)
            )
        ),

        # We make a transitive parent link only when the package is a library
        # level package.
        transitive_parent=And(Self.is_unit_root, Not(Self.is_subunit)),

        more_rules=[

            # Separate packages and nested packages basically need to be
            # treated the same way:
            # We cannot use a transitive ref because of hiding issues, so we'll
            # do a prioritary ref, that groups together the necessary envs.
            # TODO: We need to ref use clauses, as in the regular package decl
            # case.
            reference(Self.cast(AdaNode).singleton,
                      through=T.PackageBody.subunit_pkg_decl_env,
                      cond=Self.is_subunit,
                      kind=RefKind.prioritary),

            # If self is not a library level package body (and hence is a
            # nested package), we need to explicitly reference its package
            # decl, because it is not in the chain of parents.
            # The reference is non transitive because if it was it would cause
            # some visibility order issues.
            # TODO: We can regroup this ref with the following ref, making
            # body_decl_scope return a grouped env with the use clauses in it.
            reference(Self.cast(AdaNode).singleton,
                      through=T.Body.body_decl_scope,
                      cond=Not(Self.is_unit_root),
                      kind=RefKind.prioritary),

            # Since the reference to the package decl is non transitive, we
            # still want to reference the envs that are "used" there.
            reference(Self.cast(AdaNode).singleton,
                      through=T.PackageBody.package_decl_uses_clauses_envs,
                      cond=Not(Self.is_unit_root))
        ]
    )

    package_name = Field(type=T.DefiningName)
    aspects = Field(type=T.AspectSpec)
    decls = Field(type=T.DeclarativePart)
    stmts = Field(type=T.HandledStmts)
    end_name = Field(type=T.EndName)

    defining_names = Property(Entity.package_name.singleton)
    defining_env = Property(Entity.children_env)

    declarative_region = Property(Entity.decls)

    @langkit_property()
    def package_decl_uses_clauses_envs():
        """
        Return the environments for the use clauses of the package decl of this
        body. Used because they need to be explicitly referenced.
        """
        pd = Var(Entity.decl_part.cast_or_raise(T.BasePackageDecl))

        return Array([pd.public_part.use_clauses_envs,
                      pd.private_part._.use_clauses_envs]).env_group()

    @langkit_property()
    def subunit_pkg_stub_env():
        return Entity.subunit_pkg_decl_env.get(
            '__nextpart', lookup=LK.flat, categories=noprims,
        ).at(0).children_env

    @langkit_property()
    def subunit_pkg_decl_env():
        return env.bind(
            Self.initial_env,
            Entity.body_scope(True).get(Entity.name_symbol, categories=noprims)
            .find(lambda e: e.is_a(T.PackageDecl, T.GenericPackageDecl))
            .match(
                lambda pd=T.PackageDecl: pd.children_env,
                lambda gpd=T.GenericPackageDecl:
                # For generic package decls, we regroup the formal part & the
                # package decl itself, since the reference will be
                # non-transitive.
                Array([gpd.children_env, gpd.package_decl.children_env])
                .env_group(),
                lambda _: PropertyError(LexicalEnv),
            ).then(lambda public_part: public_part.get(
                '__privatepart', LK.flat, categories=noprims
            ).at(0).then(
                # If there is a private part, group it with the rest
                lambda pp: Array([pp.children_env, public_part]).env_group(),
                default_val=public_part
            ))
        )


class TaskBody(Body):
    """
    Task body.
    """

    name = Field(type=T.DefiningName)
    aspects = Field(type=T.AspectSpec)
    decls = Field(type=T.DeclarativePart)
    stmts = Field(type=T.HandledStmts)
    end_name = Field(type=T.EndName)

    defining_names = Property(Entity.name.singleton)

    env_spec = child_unit(
        name_expr='__nextpart',
        scope_expr=Entity.body_scope(True),

        dest_env=env.bind(
            Self.initial_env,
            Entity.body_scope(False, True)
            ._or(Entity.body_scope(False, False))
        ),

        more_rules=[
            reference(Self.cast(AdaNode).singleton,
                      T.TaskBody.task_type_decl_scope)
        ]
    )

    task_type_decl_scope = Property(Entity.task_type.children_env)

    @langkit_property()
    def task_type():
        return Entity.parent.node_env.get(
            Entity.name_symbol, categories=noprims
        ).find(lambda sp: sp.is_a(T.TaskTypeDecl, T.SingleTaskDecl)).match(
            lambda t=T.TaskTypeDecl: t,
            lambda t=T.SingleTaskDecl: t.task_type,
            lambda _: PropertyError(T.TaskTypeDecl.entity, "Should not happen")
        )


class ProtectedBody(Body):
    """
    Protected object body.
    """

    env_spec = child_unit(
        name_expr='__nextpart',
        scope_expr=Entity.body_scope(True),

        dest_env=env.bind(
            Self.initial_env,
            Entity.body_scope(False, True)
            ._or(Entity.body_scope(False, False))
        ),

        more_rules=[
            reference(Self.cast(AdaNode).singleton,
                      through=T.Body.body_decl_scope,
                      kind=RefKind.transitive)
        ]
    )

    name = Field(type=T.DefiningName)
    aspects = Field(type=T.AspectSpec)
    decls = Field(type=T.DeclarativePart)
    end_name = Field(type=T.EndName)

    defining_names = Property(Entity.name.singleton)


class EntryBody(Body):
    """
    Entry body.
    """

    entry_name = Field(type=T.DefiningName)
    index_spec = Field(type=T.EntryIndexSpec)
    params = Field(type=T.Params)
    barrier = Field(type=T.Expr)

    decls = Field(type=T.DeclarativePart)
    stmts = Field(type=T.HandledStmts)
    end_name = Field(type=T.EndName)
    aspects = NullField()

    defining_names = Property(Entity.entry_name.singleton)

    env_spec = EnvSpec(add_env())


class EntryIndexSpec(BasicDecl):
    """
    Index specification for an entry body.
    """

    id = Field(type=T.DefiningName)
    subtype = Field(type=T.AdaNode)
    aspects = NullField()

    env_spec = EnvSpec(add_to_env_kv(Entity.name_symbol, Self))

    defining_names = Property(Entity.id.singleton)
    defining_env = Property(Entity.expr_type.defining_env)
    expr_type = Property(Entity.subtype.match(
        lambda subt=T.SubtypeIndication: subt.designated_type,
        lambda e: e.cast_or_raise(T.Expr).expression_type,
    ))

    @langkit_property()
    def xref_equation():
        return Entity.subtype.sub_equation

    xref_entry_point = Property(True)


class Subunit(AdaNode):
    """
    Subunit (``separate``).
    """

    name = Field(type=T.Name)
    body = Field(type=T.Body)


class ProtectedBodyStub(BodyStub):
    """
    Stub for a protected object body (``is separate``).
    """

    name = Field(type=T.DefiningName)
    aspects = Field(type=T.AspectSpec)

    defining_names = Property(Entity.name.singleton)


class SubpBodyStub(BodyStub):
    """
    Stub for a subprogram body (``is separate``).
    """

    overriding = Field(type=Overriding)
    subp_spec = Field(type=T.SubpSpec)
    aspects = Field(type=T.AspectSpec)

    defining_names = Property(Entity.subp_spec.name.as_entity.singleton)
    # Note that we don't have to override the defining_env property here since
    # what we put in lexical environment is their SubpSpec child.

    env_spec = EnvSpec(
        add_to_env_kv(Entity.name_symbol, Self),
        # TODO: If subp body stubs can be separates, we need to handle that
        # here.
        add_env(),
    )

    type_expression = Property(Entity.subp_spec.returns)


class PackageBodyStub(BodyStub):
    """
    Stub for a package body (``is separate``).
    """

    name = Field(type=T.DefiningName)
    aspects = Field(type=T.AspectSpec)

    defining_names = Property(Entity.name.singleton)

    env_spec = EnvSpec(
        add_to_env_kv('__nextpart', Self, dest_env=Entity.stub_decl_env),
        add_env(),
    )

    @langkit_property()
    def stub_decl_env():
        return env.bind(
            Entity.initial_env,
            Entity.package_previous_part.then(lambda d: d.node.children_env)
        )


class TaskBodyStub(BodyStub):
    """
    Stub for a task body (``is separate``).
    """

    name = Field(type=T.DefiningName)
    aspects = Field(type=T.AspectSpec)

    defining_names = Property(Entity.name.singleton)


class LibraryItem(AdaNode):
    """
    Library item in a compilation unit.
    """

    has_private = Field(type=Private)
    item = Field(type=T.BasicDecl)


class RangeSpec(AdaNode):
    """
    Range specification.
    """

    range = Field(type=Expr)

    xref_equation = Property(Entity.range.xref_equation)


class IncompleteTypeDecl(BaseTypeDecl):
    """
    Incomplete declaration for a type.
    """

    discriminants = Field(type=T.DiscriminantPart)
    aspects = NullField()

    env_spec = EnvSpec(
        add_to_env_kv(Entity.name_symbol, Self),
        add_env()
    )

    defining_env = Property(Self.children_env, type=LexicalEnv)

    discriminants_list = Property(Entity.discriminants.abstract_formal_params)


class IncompleteTaggedTypeDecl(IncompleteTypeDecl):
    """
    Incomplete declaration for a tagged type.
    """

    has_abstract = Field(type=Abstract)

    is_tagged_type = Property(True)


class Params(AdaNode):
    """
    List of parameter specifications.
    """

    params = Field(type=ParamSpec.list)


class ParentList(Name.list):
    """
    List of parents in a type declaration.
    """

    pass


class DiscriminantChoiceList(Identifier.list):
    """
    List of discriminant associations.
    """

    pass


class AlternativesList(AdaNode.list):
    """
    List of alternatives in a ``when ...`` clause.
    """

    pass


class ExprAlternativesList(Expr.list):
    """
    List of alternatives in a membership test expression.
    """

    pass


class ConstraintList(AdaNode.list):
    """
    List of constraints.
    """

    pass


class UnconstrainedArrayIndex(AdaNode):
    """
    List of unconstrained array indexes.
    """

    subtype_indication = Field(type=SubtypeIndication)

    @langkit_property(dynamic_vars=[origin])
    def designated_type():
        return Entity.subtype_indication.designated_type
