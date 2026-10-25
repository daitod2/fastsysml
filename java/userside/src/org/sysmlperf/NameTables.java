package org.sysmlperf;

import java.util.ArrayDeque;
import java.util.Collections;
import java.util.Deque;
import java.util.HashSet;
import java.util.IdentityHashMap;
import java.util.Iterator;
import java.util.Map;
import java.util.Set;
import java.util.WeakHashMap;

import org.eclipse.emf.common.notify.Notification;
import org.eclipse.emf.common.notify.impl.AdapterImpl;
import org.eclipse.emf.ecore.EObject;
import org.eclipse.emf.ecore.EReference;
import org.eclipse.emf.ecore.resource.Resource;
import org.eclipse.emf.ecore.resource.ResourceSet;
import org.eclipse.emf.ecore.util.EContentAdapter;
import org.eclipse.emf.ecore.util.InternalEList;
import org.omg.sysml.lang.sysml.Conjugation;
import org.omg.sysml.lang.sysml.Element;
import org.omg.sysml.lang.sysml.Feature;
import org.omg.sysml.lang.sysml.Import;
import org.omg.sysml.lang.sysml.Membership;
import org.omg.sysml.lang.sysml.MembershipImport;
import org.omg.sysml.lang.sysml.Namespace;
import org.omg.sysml.lang.sysml.NamespaceImport;
import org.omg.sysml.lang.sysml.OwningMembership;
import org.omg.sysml.lang.sysml.Relationship;
import org.omg.sysml.lang.sysml.Specialization;
import org.omg.sysml.lang.sysml.SysMLPackage;
import org.omg.sysml.lang.sysml.Type;
import org.omg.sysml.lang.sysml.VisibilityKind;
import org.omg.sysml.util.FeatureUtil;
import org.omg.sysml.util.NamespaceUtil;
import org.omg.sysml.util.TypeUtil;

/**
 * Lazily computed name tables for all Namespaces, generalizing the precalculated library index: the table of a
 * Namespace contains every name that KerMLScope.resolve could find as the next segment of a qualified name
 * searched in that Namespace, so the search can skip a Namespace whose table does not contain it.
 * <p>
 * The table is computed by following the same edges as the search (owned memberships; conjugated, general,
 * implicit general and chaining types; imported namespaces and memberships, and the public descendants of
 * recursive imports), computing member names, implicit general types and additional members as the search
 * itself would. To keep this from resolving references (and changing the order in which the pilot
 * implementation computes and caches derived information, which can change its results), tables are only
 * computed for Namespaces in closed Resources: Resources with no unresolved proxies in their (non-derived)
 * references, which only reference closed Resources.
 * <p>
 * The tables are invalidated by any non-touch change notified for an object in a closed Resource (observed with
 * an EContentAdapter), and when a Resource referenced by a closed Resource is removed or unloaded.
 * <p>
 * This is the user-side counterpart of org.omg.kerml.xtext.scoping.NameTables (active mode) in the patch, using
 * no change to the pilot implementation.
 */
public class NameTables {

	public static class Stats {
		public static long checks, pruned, tablesComputed, incomplete, resourceScans, closedResources, invalidations;

		public static String summary() {
			return String.format("tables: checks=%d pruned=%d computed=%d incomplete=%d resourceScans=%d closedResources=%d invalidations=%d",
					checks, pruned, tablesComputed, incomplete, resourceScans, closedResources, invalidations);
		}
	}

	/**
	 * A name table, or (names == null) the finding that it is not available yet (because a Namespace that is
	 * not in a closed Resource was reached), which is retried after RECHECK_INTERVAL lookups.
	 */
	protected record Table(Set<String> names, long modificationCount, long epoch, long computedAtLookup) {}

	protected static class ResourceState {
		boolean isClosed;
		long checkedAtLookup;
		Set<Resource> references = Collections.emptySet();
	}

	/** Lookups (MemoScope.Stats.lookups) between rechecks of a Resource (or table) found not to be closed. */
	protected static final long RECHECK_INTERVAL = 2000;

	protected static final Map<Resource, ResourceState> states = new WeakHashMap<>();

	/** The number of non-touch changes notified for objects in closed Resources. */
	protected static long modificationCount = 0;

	/** Incremented when a Resource stops being closed, invalidating all tables. */
	protected static long epoch = 0;

	/** The Namespaces whose tables are being computed (a computation can lead to name resolution). */
	protected static final Set<Namespace> inProgress = Collections.newSetFromMap(new IdentityHashMap<>());

	/**
	 * Whether a search for the given next name segment can skip the given Namespace.
	 */
	public static boolean canSkip(Namespace ns, String segment) {
		Stats.checks++;
		Set<String> names = namesOf(ns);
		if (names != null && !names.contains(segment)) {
			Stats.pruned++;
			return true;
		}
		return false;
	}

	/**
	 * The name table of the given Namespace, or null if it is not available.
	 */
	public static Set<String> namesOf(Namespace ns) {
		Resource resource = ns.eResource();
		if (resource == null || !isClosed(resource) || inProgress.contains(ns)) {
			return null;
		}
		ScopeCache cache = ScopeCache.of(ns);
		Table table = valid(cache.table);
		if (table != null && (table.names() != null ||
				MemoScope.Stats.lookups - table.computedAtLookup() < RECHECK_INTERVAL)) {
			return table.names();
		}
		inProgress.add(ns);
		try {
			Set<String> names = compute(ns);
			if (names == null) {
				Stats.incomplete++;
			} else {
				Stats.tablesComputed++;
			}
			// The computation can add (additional) members, so the modification count is taken after it.
			cache.table = new Table(names, modificationCount, epoch, MemoScope.Stats.lookups);
			return names;
		} finally {
			inProgress.remove(ns);
		}
	}

	protected static Table valid(Table table) {
		return table != null && table.modificationCount() == modificationCount && table.epoch() == epoch? table: null;
	}

	/**
	 * Collects the names found by following the search edges from the given Namespace, using the tables of
	 * the Namespaces reached where they are available. Returns null if a Namespace that is not in a closed
	 * Resource is reached.
	 */
	protected static Set<String> compute(Namespace start) {
		Set<String> names = new HashSet<>();
		Set<Namespace> visited = Collections.newSetFromMap(new IdentityHashMap<>());
		Set<Namespace> visitedRecursively = Collections.newSetFromMap(new IdentityHashMap<>());
		Deque<Namespace> stack = new ArrayDeque<>();
		Deque<Namespace> recursiveStack = new ArrayDeque<>();
		stack.push(start);
		while (!stack.isEmpty() || !recursiveStack.isEmpty()) {
			if (!recursiveStack.isEmpty()) {
				// Public descendants of a recursively imported Namespace are each searched in full.
				Namespace ns = recursiveStack.pop();
				if (visitedRecursively.add(ns)) {
					for (Relationship relationship: ns.getOwnedRelationship()) {
						if (relationship instanceof OwningMembership &&
								((OwningMembership)relationship).getVisibility() == VisibilityKind.PUBLIC &&
								((OwningMembership)relationship).getOwnedMemberElement() instanceof Namespace) {
							Namespace descendant = (Namespace)((OwningMembership)relationship).getOwnedMemberElement();
							stack.push(descendant);
							recursiveStack.push(descendant);
						}
					}
				}
				continue;
			}
			Namespace ns = stack.pop();
			if (!visited.add(ns)) {
				continue;
			}
			if (ns != start) {
				Resource resource = ns.eResource();
				if (resource == null || !isClosed(resource)) {
					return null;
				}
				Table table = inProgress.contains(ns)? null: valid(ScopeCache.of(ns).table);
				if (table != null && table.names() != null) {
					names.addAll(table.names());
					continue;
				}
			}

			// owned
			NamespaceUtil.addAdditionalMembersTo(ns);
			for (Membership membership: ns.getOwnedMembership()) {
				addName(names, membership.getMemberName());
				addName(names, membership.getMemberShortName());
			}

			// gen
			if (ns instanceof Type) {
				Type type = (Type)ns;
				Conjugation conjugator = type.getOwnedConjugator();
				if (conjugator != null) {
					push(stack, conjugator.getOriginalType());
				}
				for (Specialization specialization: type.getOwnedSpecialization()) {
					push(stack, specialization.getGeneral());
				}
				for (Type implicitType: TypeUtil.getImplicitGeneralTypesFor(type)) {
					push(stack, implicitType);
				}
				if (type instanceof Feature) {
					push(stack, FeatureUtil.getLastChainingFeatureOf((Feature)type));
				}
			}

			// imp
			for (Import imp: ns.getOwnedImport()) {
				if (imp instanceof NamespaceImport) {
					Namespace imported = ((NamespaceImport)imp).getImportedNamespace();
					push(stack, imported);
					if (imp.isRecursive() && imported != null) {
						recursiveStack.push(imported);
					}
				} else if (imp instanceof MembershipImport) {
					Membership membership = ((MembershipImport)imp).getImportedMembership();
					if (membership != null) {
						addName(names, membership.getMemberName());
						addName(names, membership.getMemberShortName());
						Element memberElement = membership.getMemberElement();
						if (imp.isRecursive() && memberElement instanceof Namespace) {
							push(stack, (Namespace)memberElement);
							recursiveStack.push((Namespace)memberElement);
						}
					}
				}
			}
		}
		return names;
	}

	protected static void push(Deque<Namespace> stack, Namespace ns) {
		if (ns != null) {
			stack.push(ns);
		}
	}

	protected static void addName(Set<String> names, String name) {
		if (name != null) {
			names.add(name);
		}
	}

	// Closed Resources

	protected static boolean isKnownClosed(Resource resource) {
		ResourceState state = resource == null? null: states.get(resource);
		return state != null && state.isClosed;
	}

	protected static boolean isClosed(Resource resource) {
		ResourceState state = states.get(resource);
		if (state != null && (state.isClosed || MemoScope.Stats.lookups - state.checkedAtLookup < RECHECK_INTERVAL)) {
			return state.isClosed;
		}
		Set<Resource> checking = Collections.newSetFromMap(new IdentityHashMap<>());
		boolean isClosed = check(resource, checking);
		if (!isClosed) {
			// Resources found closed only on the assumption that this one was closed are not closed either.
			for (Resource checked: checking) {
				ResourceState checkedState = states.get(checked);
				if (checkedState != null) {
					checkedState.isClosed = false;
				}
			}
		}
		return isClosed;
	}

	/**
	 * A Resource is closed if none of the (non-derived) references of its contents is an unresolved proxy,
	 * and all the Resources referenced are closed (Resources in a cycle are assumed closed while checking).
	 */
	protected static boolean check(Resource resource, Set<Resource> checking) {
		ResourceState state = states.get(resource);
		if (state != null && state.isClosed) {
			return true;
		}
		if (!checking.add(resource)) {
			return true;
		}
		Stats.resourceScans++;
		state = new ResourceState();
		state.checkedAtLookup = MemoScope.Stats.lookups;
		states.put(resource, state);
		Set<Resource> references = Collections.newSetFromMap(new IdentityHashMap<>());
		boolean isClosed = scan(resource, references);
		if (isClosed) {
			for (Resource reference: references) {
				if (!check(reference, checking)) {
					isClosed = false;
					break;
				}
			}
		}
		state.references = references;
		state.isClosed = isClosed;
		if (isClosed) {
			Stats.closedResources++;
			watch(resource);
		}
		return isClosed;
	}

	protected static boolean scan(Resource resource, Set<Resource> references) {
		for (Iterator<EObject> contents = resource.getAllContents(); contents.hasNext();) {
			EObject object = contents.next();
			for (EReference reference: object.eClass().getEAllReferences()) {
				if (reference.isContainment() || reference.isContainer() || reference.isDerived() ||
						// Not marked derived, but computed from other references by each kind of Relationship,
						// resolving them (e.g., Specialization.target gets the general).
						reference == SysMLPackage.Literals.RELATIONSHIP__SOURCE ||
						reference == SysMLPackage.Literals.RELATIONSHIP__TARGET) {
					continue;
				}
				Object value = object.eGet(reference, false);
				if (reference.isMany()) {
					for (Iterator<?> values = ((InternalEList<?>)value).basicIterator(); values.hasNext();) {
						if (!isResolved(values.next(), resource, references)) {
							return false;
						}
					}
				} else if (!isResolved(value, resource, references)) {
					return false;
				}
			}
		}
		return true;
	}

	protected static boolean isResolved(Object value, Resource resource, Set<Resource> references) {
		if (value instanceof EObject) {
			EObject target = (EObject)value;
			if (target.eIsProxy()) {
				return false;
			}
			Resource targetResource = target.eResource();
			if (targetResource != null && targetResource != resource) {
				references.add(targetResource);
			}
		}
		return true;
	}

	/**
	 * Counts the non-touch changes of the contents of closed Resources.
	 */
	protected static final EContentAdapter MODIFICATION_COUNTER = new EContentAdapter() {
		@Override
		public void notifyChanged(Notification notification) {
			super.notifyChanged(notification);
			if (!notification.isTouch() && notification.getNotifier() instanceof EObject) {
				modificationCount++;
			} else if (notification.getNotifier() instanceof Resource &&
					notification.getFeatureID(Resource.class) == Resource.RESOURCE__IS_LOADED &&
					!((Resource)notification.getNotifier()).isLoaded()) {
				released((Resource)notification.getNotifier());
			}
		}

		@Override
		public boolean isAdapterForType(Object type) {
			return type == NameTables.class;
		}
	};

	/**
	 * Observes the changes of a closed Resource, and the removal of Resources from its ResourceSet.
	 */
	protected static void watch(Resource resource) {
		if (!resource.eAdapters().contains(MODIFICATION_COUNTER)) {
			resource.eAdapters().add(MODIFICATION_COUNTER);
		}
		ResourceSet resourceSet = resource.getResourceSet();
		if (resourceSet != null && !resourceSet.eAdapters().contains(RESOURCE_SET_WATCHER)) {
			resourceSet.eAdapters().add(RESOURCE_SET_WATCHER);
		}
	}

	protected static final AdapterImpl RESOURCE_SET_WATCHER = new AdapterImpl() {
		@Override
		public void notifyChanged(Notification notification) {
			if (notification.getFeatureID(ResourceSet.class) == ResourceSet.RESOURCE_SET__RESOURCES) {
				if (notification.getEventType() == Notification.REMOVE && notification.getOldValue() instanceof Resource) {
					released((Resource)notification.getOldValue());
				} else if (notification.getEventType() == Notification.REMOVE_MANY ||
						notification.getEventType() == Notification.SET) {
					releasedAll();
				}
			}
		}

		@Override
		public boolean isAdapterForType(Object type) {
			return type == NameTables.class;
		}
	};

	protected static void released(Resource released) {
		ResourceState releasedState = states.remove(released);
		if (releasedState == null || !releasedState.isClosed) {
			// Closed Resources only reference closed Resources.
			return;
		}
		released.eAdapters().remove(MODIFICATION_COUNTER);
		// The tables of the released Resource's own Namespaces go with it, but the tables of the Namespaces
		// of the closed Resources referencing it include names from it.
		boolean invalidate = false;
		for (ResourceState state: states.values()) {
			if (state.isClosed && state.references.contains(released)) {
				state.isClosed = false;
				invalidate = true;
			}
		}
		if (invalidate) {
			Stats.invalidations++;
			epoch++;
		}
	}

	protected static void releasedAll() {
		states.clear();
		Stats.invalidations++;
		epoch++;
	}
}
