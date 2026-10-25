package org.sysmlperf;

import java.util.ArrayDeque;
import java.util.ArrayList;
import java.util.Collections;
import java.util.Deque;
import java.util.IdentityHashMap;
import java.util.List;
import java.util.Map;
import java.util.Set;

import org.eclipse.emf.ecore.EClass;
import org.eclipse.xtext.naming.QualifiedName;
import org.eclipse.xtext.resource.IEObjectDescription;
import org.eclipse.xtext.scoping.IScope;
import org.omg.kerml.xtext.naming.QualifiedNameUtil;
import org.omg.kerml.xtext.scoping.KerMLScope;
import org.omg.kerml.xtext.scoping.KerMLScopeProvider;
import org.omg.sysml.lang.sysml.Conjugation;
import org.omg.sysml.lang.sysml.Element;
import org.omg.sysml.lang.sysml.Import;
import org.omg.sysml.lang.sysml.Membership;
import org.omg.sysml.lang.sysml.Namespace;
import org.omg.sysml.lang.sysml.Redefinition;
import org.omg.sysml.lang.sysml.Relationship;
import org.omg.sysml.lang.sysml.Specialization;
import org.omg.sysml.lang.sysml.SysMLPackage;

/**
 * A KerMLScope that memoizes the result of resolving a name in its local scope (that is, without the
 * outer scopes), in a table kept for its Namespace (in its ScopeCache adapter). The outer scopes are themselves MemoKerMLScopes,
 * so the resolution of a name in a chain of nested scopes is memoized level by level.
 * <p>
 * Besides the Namespace and the name, the local result depends on the scope flags and possibly on
 * the context Element and the skip Element, which are made part of the key as follows:
 * <ul>
 * <li>The context Element only matters in the first scope, for its own membership in the Namespace,
 * when it has no declared name or short name (its declared names are used rather than its effective
 * names), so it is only part of the key then.</li>
 * <li>A skip Redefinition is always part of the key. Otherwise, the skip Element only matters if the
 * search scans the ownedRelationships of the Element that owns it (it is excluded as visited). So the
 * Namespaces scanned are recorded (the footprint), and a result whose footprint does not include the
 * owner of the skip Element is shared by all skip Elements whose owners are not in the footprint.</li>
 * </ul>
 * Only searches started with no visited Elements in the scope provider are memoized, since visited
 * Elements (during a nested resolution) are excluded from the search.
 * <p>
 * A result is only kept if it is final. A search during which some nested resolution found nothing
 * (typically because of a cyclic dependency on a reference still being resolved) may find something
 * else later. And a reference that is not found in any scope is retried by the linker later, when it
 * may be found, so a local result of finding nothing is only kept once the name has been found in an
 * outer scope of the same lookup.
 * <p>
 * The search is also pruned with NameTables. This is the user-side counterpart of
 * org.omg.kerml.xtext.scoping.MemoKerMLScope in the patch, using no change to the pilot implementation.
 */
public class MemoScope extends KerMLScope {

	public static class Stats {
		public static long lookups, bypassNested, bypassGlobal, bypassNoAdapter;
		public static long sharedHits, exactHits, misses, stored, provisional, notFoundStored, notFoundDropped, footprintTotal;

		public static String summary() {
			return String.format(
				"lookups=%d bypass(nested=%d global=%d noAdapter=%d) hits(shared=%d exact=%d) misses=%d "
				+ "stored=%d (notFound kept=%d dropped=%d) provisional=%d avgFootprint=%.1f",
				lookups, bypassNested, bypassGlobal, bypassNoAdapter, sharedHits, exactHits, misses,
				stored, notFoundStored, notFoundDropped, provisional, misses == 0? 0.0: (double)footprintTotal / misses);
		}
	}

	protected record Key(EClass referenceType, boolean isInsideScope, boolean isFirstScope, boolean isRedefinition,
			boolean includeAll, Element element, Element skip, QualifiedName name) {}

	protected record Result(IEObjectDescription description, boolean isShadowing, Set<Namespace> footprint) {}

	protected record Entry(Map<Object, Object> table, Key key, Result result) {}

	/**
	 * A memoized search in progress. Nested searches can start while the visited Elements are momentarily
	 * empty, and resolutions in other languages (scope providers) can be nested in a search.
	 */
	protected static class Frame {
		final KerMLScopeProvider scopeProvider;
		final Set<Namespace> footprint = Collections.newSetFromMap(new IdentityHashMap<>());
		boolean isProvisional = false;

		Frame(KerMLScopeProvider scopeProvider) {
			this.scopeProvider = scopeProvider;
		}
	}

	protected static final Deque<Frame> activeFrames = new ArrayDeque<>();

	/** Whether to memoize local results, and whether to prune the search with NameTables. */
	public static boolean useMemo = true, useNameTables = true;

	/**
	 * For each lookup being evaluated (innermost first), the local results of finding nothing, to be kept
	 * only if the name is then found in an outer scope.
	 */
	protected static final Deque<List<Entry>> pendingNotFound = new ArrayDeque<>();

	/**
	 * Whether the next call of getSingleElement is the delegation to the outer scope within a lookup,
	 * rather than the start of a new lookup.
	 */
	protected static boolean isOuterScopeCall = false;

	public MemoScope(IScope parent, Namespace ns, EClass referenceType, KerMLScopeProvider scopeProvider,
			boolean isInsideScope, boolean isFirstScope, boolean isRedefinition, Element element, Element skip) {
		super(parent, ns, referenceType, scopeProvider, isInsideScope, isFirstScope, isRedefinition, element, skip);
	}

	@Override
	public IEObjectDescription getSingleElement(QualifiedName name) {
		boolean isLookupStart = !isOuterScopeCall;
		isOuterScopeCall = false;
		if (!isLookupStart) {
			return memoGetSingleElement(name);
		}
		pendingNotFound.push(new ArrayList<>());
		IEObjectDescription description = null;
		try {
			description = memoGetSingleElement(name);
		} finally {
			List<Entry> notFound = pendingNotFound.pop();
			if (description != null) {
				for (Entry entry: notFound) {
					entry.table().putIfAbsent(entry.key(), entry.result());
				}
				Stats.notFoundStored += notFound.size();
			} else {
				Stats.notFoundDropped += notFound.size();
				// A (nested) resolution found nothing, so any search it is nested in is provisional.
				for (Frame frame: activeFrames) {
					frame.isProvisional = true;
				}
			}
		}
		return description;
	}

	protected IEObjectDescription memoGetSingleElement(QualifiedName name) {
		Stats.lookups++;
		if (!useMemo) {
			return super.getSingleElement(name);
		}
		if (QualifiedNameUtil.isGlobalScopeQualification(name)) {
			Stats.bypassGlobal++;
			return super.getSingleElement(name);
		}
		if (!scopeProvider.getVisited().isEmpty()) {
			Stats.bypassNested++;
			return super.getSingleElement(name);
		}
		Map<Object, Object> table = ScopeCache.of(ns).memo;

		Result result = lookup(table, name);
		if (result.isShadowing()) {
			isShadowing = true;
		}
		if (result.description() != null) {
			return result.description();
		} else if (getParent() != null && !isShadowing) {
			isOuterScopeCall = getParent() instanceof MemoScope;
			try {
				return getParent().getSingleElement(name);
			} finally {
				isOuterScopeCall = false;
			}
		} else {
			return null;
		}
	}

	protected Result lookup(Map<Object, Object> table, QualifiedName name) {
		boolean includeAll = referenceType == SysMLPackage.eINSTANCE.getMembership() &&
				element instanceof Import && ((Import)element).isImportAll();
		Element elementKey = isFirstScope && element != null && element.getOwningNamespace() == ns &&
				element.getDeclaredName() == null && element.getDeclaredShortName() == null? element: null;
		Element skipOwner = getSkipOwner();

		boolean skipInKey = skip instanceof Redefinition;
		Key sharedKey = new Key(referenceType, isInsideScope, isFirstScope, isRedefinition, includeAll, elementKey,
				skipInKey? skip: null, name);
		Result shared = (Result)table.get(sharedKey);
		if (shared != null && (skipInKey || skipOwner == null || !shared.footprint().contains(skipOwner))) {
			Stats.sharedHits++;
			addToActiveFootprints(shared.footprint());
			return shared;
		}
		Key exactKey = null;
		if (skip != null && !skipInKey) {
			exactKey = new Key(referenceType, isInsideScope, isFirstScope, isRedefinition, includeAll, elementKey, skip, name);
			Result exact = (Result)table.get(exactKey);
			if (exact != null) {
				Stats.exactHits++;
				addToActiveFootprints(exact.footprint());
				return exact;
			}
		}

		Stats.misses++;
		Frame frame = new Frame(scopeProvider);
		Result result = compute(name, frame);
		Stats.footprintTotal += result.footprint().size();
		Key key = shared == null && (skipInKey || skipOwner == null || !result.footprint().contains(skipOwner))?
				sharedKey: exactKey;
		if (frame.isProvisional) {
			Stats.provisional++;
		} else if (key != null) {
			if (result.description() != null) {
				table.put(key, result);
				Stats.stored++;
			} else if (!pendingNotFound.isEmpty()) {
				pendingNotFound.peek().add(new Entry(table, key, result));
			}
		}
		return result;
	}

	/**
	 * The Element whose ownedRelationships include the skip Element, if the search could encounter the
	 * skip Element there (that is, if it is a Membership, Import, Specialization or Conjugation).
	 */
	protected Element getSkipOwner() {
		if (skip instanceof Membership || skip instanceof Import || skip instanceof Specialization || skip instanceof Conjugation) {
			return ((Relationship)skip).getOwningRelatedElement();
		}
		return null;
	}

	protected Result compute(QualifiedName name, Frame frame) {
		boolean wasShadowing = isShadowing;
		isShadowing = false;
		activeFrames.push(frame);
		try {
			IEObjectDescription first = null;
			for (IEObjectDescription description: resolveInScope(name, true)) {
				first = description;
				break;
			}
			return new Result(first, isShadowing, frame.footprint);
		} finally {
			activeFrames.pop();
			isShadowing |= wasShadowing;
		}
	}

	/**
	 * The skip Element of a search is also excluded in the nested resolutions done during it, so the
	 * Namespaces they scan are added to the footprints of all the searches in progress for the same
	 * scope provider.
	 */
	protected void addToActiveFootprints(Namespace ns) {
		for (Frame frame: activeFrames) {
			if (frame.scopeProvider == scopeProvider) {
				frame.footprint.add(ns);
			}
		}
	}

	protected void addToActiveFootprints(Set<Namespace> namespaces) {
		for (Frame frame: activeFrames) {
			if (frame.scopeProvider == scopeProvider) {
				frame.footprint.addAll(namespaces);
			}
		}
	}

	/**
	 * Like the library index check in KerMLScope.resolve, skip a Namespace (other than the one of this scope)
	 * that cannot contain the next segment of the qualified name searched for, using its name table.
	 */
	@Override
	protected boolean resolve(Namespace ns, QualifiedName qn, Set<Namespace> ownedVisited, Set<Namespace> visited,
			Set<Element> redefined, boolean checkIfAdded, boolean isInsideScope, boolean isInheriting,
			boolean includeImplicitGen, boolean includeAll) {
		if (ns != this.ns && targetqn != null && targetqn.startsWith(qn) && targetqn.getSegmentCount() > qn.getSegmentCount()) {
			String segment = targetqn.getSegment(qn.getSegmentCount());
			if (useNameTables && NameTables.canSkip(ns, segment)) {
				return false;
			}
		}
		return super.resolve(ns, qn, ownedVisited, visited, redefined, checkIfAdded, isInsideScope, isInheriting,
				includeImplicitGen, includeAll);
	}

	@Override
	protected boolean owned(Namespace ns, QualifiedName qn, Set<Namespace> ownedvisited, Set<Namespace> visited,
			Set<Element> redefined, boolean checkIfAdded, boolean isInsideScope, boolean isInheriting,
			boolean includeImplicitGen, boolean includeAll) {
		addToActiveFootprints(ns);
		return super.owned(ns, qn, ownedvisited, visited, redefined, checkIfAdded, isInsideScope, isInheriting,
				includeImplicitGen, includeAll);
	}

	@Override
	protected boolean gen(Namespace ns, QualifiedName qn, Set<Namespace> visited, Set<Element> redefined,
			boolean isInheriting, boolean includeImplicit) {
		addToActiveFootprints(ns);
		return super.gen(ns, qn, visited, redefined, isInheriting, includeImplicit);
	}

	@Override
	protected boolean imp(Namespace ns, QualifiedName qn, Set<Namespace> visited, boolean isInsideScope,
			boolean includeImplicitGen, boolean includeAll) {
		addToActiveFootprints(ns);
		return super.imp(ns, qn, visited, isInsideScope, includeImplicitGen, includeAll);
	}

}
