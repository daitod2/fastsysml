package org.sysmlperf;

import java.util.Set;

import org.eclipse.emf.ecore.EClass;
import org.eclipse.emf.ecore.EClassifier;
import org.eclipse.emf.ecore.EStructuralFeature;
import org.eclipse.xtext.scoping.IScopeProvider;
import org.omg.kerml.xtext.KerMLRuntimeModule;
import org.omg.kerml.xtext.KerMLStandaloneSetup;
import org.omg.sysml.delegate.setting.DerivedPropertySettingDelegateFactory;
import org.omg.sysml.lang.sysml.SysMLPackage;
import org.omg.sysml.xtext.SysMLRuntimeModule;
import org.omg.sysml.xtext.SysMLStandaloneSetup;

import com.google.inject.Guice;
import com.google.inject.Injector;
import com.google.inject.Module;
import com.google.inject.util.Modules;

/**
 * Registers the KerML and SysML languages with the user-side performance improvements, using only extension
 * points of the pilot implementation (the jar is not changed and no class of it is replaced):
 * <ul>
 * <li>"delegates": derived property setting delegates replaced through the EMF setting delegate factory
 * registry (FastDelegateFactory).</li>
 * <li>"memo": name resolution memoization, by binding the scope providers with Guice (MemoScopeProviders).</li>
 * <li>"tables": lazily computed name tables for pruning the name resolution search (NameTables).</li>
 * </ul>
 * The precalculated library index (.index.json in the library folder) is the remaining user-side improvement,
 * which needs no code.
 */
public class UserSideSetup {

	public static void setup(Set<String> components) {
		boolean delegates = components.contains("delegates");
		boolean memo = components.contains("memo");
		boolean tables = components.contains("tables");
		MemoScope.useMemo = memo;
		MemoScope.useNameTables = tables;

		if (memo || tables) {
			new KerMLStandaloneSetup() {
				@Override
				public Injector createInjector() {
					return Guice.createInjector(override(new KerMLRuntimeModule(), MemoScopeProviders.KerML.class));
				}
			}.createInjectorAndDoEMFRegistration();
			new SysMLStandaloneSetup() {
				@Override
				public Injector createInjector() {
					return Guice.createInjector(override(new SysMLRuntimeModule(), MemoScopeProviders.SysML.class));
				}
			}.createInjectorAndDoEMFRegistration();
		} else {
			KerMLStandaloneSetup.doSetup();
			SysMLStandaloneSetup.doSetup();
		}

		if (delegates) {
			// After the language setup, which registers the pilot implementation's factory, and before any model
			// object is created. Features cache their delegates, so any cached ones are reset.
			EStructuralFeature.Internal.SettingDelegate.Factory.Registry.INSTANCE.put(
					DerivedPropertySettingDelegateFactory.SYSML_ANNOTATION, new FastDelegateFactory());
			for (EClassifier classifier: SysMLPackage.eINSTANCE.getEClassifiers()) {
				if (classifier instanceof EClass) {
					for (EStructuralFeature feature: ((EClass)classifier).getEStructuralFeatures()) {
						((EStructuralFeature.Internal)feature).setSettingDelegate(null);
					}
				}
			}
		}

		Runtime.getRuntime().addShutdownHook(new Thread(() -> {
			if (memo || tables) {
				System.out.println("userside: memo: " + MemoScope.Stats.summary());
			}
			if (tables) {
				System.out.println("userside: " + NameTables.Stats.summary());
			}
		}));
		System.out.println("userside: " + components);
	}

	static Module override(Module runtimeModule, Class<? extends IScopeProvider> provider) {
		return Modules.override(runtimeModule).with(binder -> binder.bind(IScopeProvider.class).to(provider));
	}
}
