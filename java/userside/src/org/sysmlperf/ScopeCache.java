package org.sysmlperf;

import java.util.HashMap;
import java.util.Map;

import org.eclipse.emf.common.notify.Adapter;
import org.eclipse.emf.common.notify.impl.AdapterImpl;
import org.omg.sysml.lang.sysml.Namespace;

/**
 * The name resolution information kept for a Namespace: memoized local scope results (MemoScope) and the name
 * table (NameTables). It is an EMF adapter of the Namespace, so it goes away with it (adapters are removed when
 * a Resource is unloaded), rather than being kept in a map from Namespaces.
 */
public class ScopeCache extends AdapterImpl {

	final Map<Object, Object> memo = new HashMap<>();
	NameTables.Table table = null;

	public static ScopeCache of(Namespace ns) {
		for (Adapter adapter: ns.eAdapters()) {
			if (adapter instanceof ScopeCache) {
				return (ScopeCache)adapter;
			}
		}
		ScopeCache cache = new ScopeCache();
		ns.eAdapters().add(cache);
		return cache;
	}

	@Override
	public boolean isAdapterForType(Object type) {
		return type == ScopeCache.class;
	}
}
