package org.sysmlperf;

import java.util.List;

import org.eclipse.emf.common.util.EList;
import org.eclipse.emf.ecore.EAnnotation;
import org.eclipse.emf.ecore.EObject;
import org.eclipse.emf.ecore.EStructuralFeature;
import org.eclipse.emf.ecore.InternalEObject;
import org.omg.sysml.delegate.setting.BasicDerivedListSettingDelegate;
import org.omg.sysml.delegate.setting.BasicDerivedObjectSettingDelegate;
import org.omg.sysml.delegate.setting.DerivedPropertySettingDelegateFactory;
import org.omg.sysml.lang.sysml.Conjugation;
import org.omg.sysml.lang.sysml.Element;
import org.omg.sysml.lang.sysml.Membership;
import org.omg.sysml.lang.sysml.Namespace;
import org.omg.sysml.lang.sysml.Relationship;
import org.omg.sysml.lang.sysml.Specialization;
import org.omg.sysml.lang.sysml.SysMLPackage;
import org.omg.sysml.lang.sysml.Type;
import org.omg.sysml.util.NonNotifyingEObjectEList;

/**
 * Replaces some of the pilot implementation's derived property setting delegates, through the EMF setting
 * delegate factory registry (no change to the pilot implementation itself):
 * <ul>
 * <li>Derived lists that subset another feature (by default a DerivedEObjectEList view, which evaluates the
 * source feature, itself usually derived, on every isEmpty, size and iteration) return a list computed once.</li>
 * <li>Type::ownedSpecialization, Type::ownedConjugator and Namespace::member use plain loops instead of streams.</li>
 * </ul>
 * The results are the same; see the patched DefaultDerivedPropertySettingDelegate and setting delegates.
 */
public class FastDelegateFactory extends DerivedPropertySettingDelegateFactory {

	@Override
	public EStructuralFeature.Internal.SettingDelegate createSettingDelegate(EStructuralFeature feature) {
		if (feature == SysMLPackage.Literals.TYPE__OWNED_SPECIALIZATION) {
			return new OwnedSpecialization(feature);
		} else if (feature == SysMLPackage.Literals.TYPE__OWNED_CONJUGATOR) {
			return new OwnedConjugator(feature);
		} else if (feature == SysMLPackage.Literals.NAMESPACE__MEMBER) {
			return new Member(feature);
		} else if (getDelegateConstructor(feature) == null) {
			return new SubsetSnapshot(feature);
		} else {
			return super.createSettingDelegate(feature);
		}
	}

	/** Like DefaultDerivedPropertySettingDelegate, but returning a list rather than a view. */
	public static class SubsetSnapshot extends BasicDerivedListSettingDelegate {

		private final Class<?> type;
		private final int featureID;
		private final int sourceFeatureID;

		public SubsetSnapshot(EStructuralFeature feature) {
			super(feature);
			type = feature.getEType().getInstanceClass();
			featureID = feature.getFeatureID();
			EAnnotation annotation = feature.getEAnnotation("subsets");
			sourceFeatureID = ((EStructuralFeature)annotation.getReferences().get(0)).getFeatureID();
		}

		@Override
		protected EList<?> basicGet(InternalEObject owner) {
			EList<Object> values = new NonNotifyingEObjectEList<>(type, owner, featureID);
			Object source = owner.eGet(sourceFeatureID, true, true);
			if (source instanceof List) {
				for (Object value: (List<?>)source) {
					if (type.isInstance(value)) {
						values.add(value);
					}
				}
			} else if (type.isInstance(source)) {
				values.add(source);
			}
			return values;
		}
	}

	public static class OwnedSpecialization extends BasicDerivedListSettingDelegate {

		public OwnedSpecialization(EStructuralFeature feature) {
			super(feature);
		}

		@Override
		protected EList<?> basicGet(InternalEObject owner) {
			EList<Specialization> specializations = new NonNotifyingEObjectEList<>(Specialization.class, owner,
					eStructuralFeature.getFeatureID());
			for (Relationship relationship: ((Type)owner).getOwnedRelationship()) {
				if (relationship instanceof Specialization && ((Specialization)relationship).getSpecific() == owner) {
					specializations.add((Specialization)relationship);
				}
			}
			return specializations;
		}
	}

	public static class OwnedConjugator extends BasicDerivedObjectSettingDelegate {

		public OwnedConjugator(EStructuralFeature feature) {
			super(feature);
		}

		@Override
		protected EObject basicGet(InternalEObject owner) {
			for (Relationship relationship: ((Type)owner).getOwnedRelationship()) {
				if (relationship instanceof Conjugation) {
					return relationship;
				}
			}
			return null;
		}
	}

	public static class Member extends BasicDerivedListSettingDelegate {

		public Member(EStructuralFeature feature) {
			super(feature);
		}

		@Override
		protected EList<?> basicGet(InternalEObject owner) {
			EList<Element> members = new NonNotifyingEObjectEList<>(Element.class, owner, eStructuralFeature.getFeatureID());
			for (Membership membership: ((Namespace)owner).getMembership()) {
				Element memberElement = membership.getMemberElement();
				if (memberElement != null) {
					members.add(memberElement);
				}
			}
			return members;
		}
	}
}
