package org.sysmlperf;

import org.eclipse.emf.ecore.EReference;
import org.eclipse.xtext.scoping.IScope;
import org.omg.kerml.xtext.scoping.KerMLScope;
import org.omg.kerml.xtext.scoping.KerMLScopeProvider;
import org.omg.sysml.lang.sysml.Element;
import org.omg.sysml.lang.sysml.Namespace;
import org.omg.sysml.xtext.scoping.SysMLScopeProvider;

/**
 * Scope providers whose scopes are MemoScopes, bound in place of KerMLScopeProvider and SysMLScopeProvider
 * with Guice. KerMLScopeProvider.scopeFor creates the outer scopes by calling scopeFor itself, so they are
 * MemoScopes too.
 */
public class MemoScopeProviders {

	public static class KerML extends KerMLScopeProvider {
		@Override
		public IScope scopeFor(Namespace pack, EReference reference, Element element, boolean isInsideScope,
				boolean isFirstScope, boolean isRedefinition, Element skip) {
			return memoScopeFor(this, super.scopeFor(pack, reference, element, isInsideScope, isFirstScope, isRedefinition, skip),
					pack, reference, element, isInsideScope, isFirstScope, isRedefinition, skip);
		}
	}

	public static class SysML extends SysMLScopeProvider {
		@Override
		public IScope scopeFor(Namespace pack, EReference reference, Element element, boolean isInsideScope,
				boolean isFirstScope, boolean isRedefinition, Element skip) {
			return memoScopeFor(this, super.scopeFor(pack, reference, element, isInsideScope, isFirstScope, isRedefinition, skip),
					pack, reference, element, isInsideScope, isFirstScope, isRedefinition, skip);
		}
	}

	/**
	 * Replaces the KerMLScope created by KerMLScopeProvider.scopeFor with an equivalent MemoScope.
	 */
	static IScope memoScopeFor(KerMLScopeProvider scopeProvider, IScope scope, Namespace pack, EReference reference,
			Element element, boolean isInsideScope, boolean isFirstScope, boolean isRedefinition, Element skip) {
		return new MemoScope(((KerMLScope)scope).getParent(), pack, reference.getEReferenceType(), scopeProvider,
				isInsideScope, isFirstScope, isRedefinition, element, skip);
	}
}
