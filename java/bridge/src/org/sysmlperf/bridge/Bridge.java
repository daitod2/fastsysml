package org.sysmlperf.bridge;

import java.io.BufferedReader;
import java.io.ByteArrayInputStream;
import java.io.File;
import java.io.FileDescriptor;
import java.io.FileOutputStream;
import java.io.IOException;
import java.io.InputStreamReader;
import java.io.PrintStream;
import java.io.StringWriter;
import java.nio.charset.StandardCharsets;
import java.util.ArrayList;
import java.util.Arrays;
import java.util.Collection;
import java.util.HashSet;
import java.util.IdentityHashMap;
import java.util.Iterator;
import java.util.List;
import java.util.Map;
import java.util.Set;

import org.eclipse.emf.common.util.EList;
import org.eclipse.emf.common.util.URI;
import org.eclipse.emf.ecore.EAttribute;
import org.eclipse.emf.ecore.EClass;
import org.eclipse.emf.ecore.EClassifier;
import org.eclipse.emf.ecore.EObject;
import org.eclipse.emf.ecore.EPackage;
import org.eclipse.emf.ecore.EReference;
import org.eclipse.emf.ecore.EStructuralFeature;
import org.eclipse.emf.ecore.resource.Resource;
import org.eclipse.emf.ecore.util.EcoreUtil;
import org.eclipse.emf.ecore.util.InternalEList;
import org.eclipse.xtext.EcoreUtil2;
import org.eclipse.xtext.nodemodel.ICompositeNode;
import org.eclipse.xtext.nodemodel.INode;
import org.eclipse.xtext.nodemodel.util.NodeModelUtils;
import org.eclipse.xtext.resource.XtextResource;
import org.eclipse.xtext.resource.impl.ResourceDescriptionsData;
import org.eclipse.xtext.scoping.IScopeProvider;
import org.eclipse.xtext.util.CancelIndicator;
import org.eclipse.xtext.validation.CheckMode;
import org.eclipse.xtext.validation.Issue;
import org.omg.kerml.xtext.KerMLRuntimeModule;
import org.omg.kerml.xtext.KerMLStandaloneSetup;
import org.omg.sysml.io.SysMLUtil;
import org.omg.sysml.lang.sysml.Element;
import org.omg.sysml.lang.sysml.Membership;
import org.omg.sysml.lang.sysml.OwningMembership;
import org.omg.sysml.lang.sysml.SysMLPackage;
import org.omg.sysml.util.SysMLLibraryUtil;
import org.omg.sysml.xtext.SysMLRuntimeModule;
import org.omg.sysml.xtext.SysMLStandaloneSetup;

import com.google.gson.JsonArray;
import com.google.gson.JsonElement;
import com.google.gson.JsonObject;
import com.google.gson.JsonParser;
import com.google.gson.stream.JsonWriter;
import com.google.inject.Guice;
import com.google.inject.Injector;
import com.google.inject.Module;
import com.google.inject.util.Modules;

/**
 * A long-running process that parses SysML/KerML for the Python binding (fastsysml), using the SysML v2
 * Pilot Implementation. Requests and responses are JSON objects, one per line, on stdin and stdout (anything
 * else printed by the pilot implementation goes to stderr).
 * <p>
 * Usage: Bridge --library &lt;sysml.library dir&gt; [--mode patched|userside|plain] [--tables] [--preload]
 * [--preresolve]
 * <ul>
 * <li>patched: the improved pilot classes (build-patched/, ahead of the jar on the classpath) with name
 * resolution memoization (and name tables with --tables).</li>
 * <li>userside: the unchanged jar with the user-side improvements (build-userside/).</li>
 * <li>plain: the unchanged pilot implementation.</li>
 * </ul>
 * Requests: {"op": "parse", "inputs": [{"path": ...} | {"text": ..., "name": ...}], "resolve": "none"|"lazy"|"all",
 * "validate": bool, "derived": [feature names], "positions": bool, "keep": bool, "uris": bool},
 * {"op": "get", "uris": [element URIs], "subtree": bool, "derived": [...], "positions": bool},
 * {"op": "metaclasses"}, {"op": "unload"}, {"op": "stats"}, {"op": "shutdown"}.
 * <p>
 * With "uris", each element also has its URI fragment ("fragment"), so that references to it from other models
 * (written with its URI) can be matched. A reference to an element of another model is its URI (and, if resolved,
 * its qualified name; for a membership, those of its member, with "memberUri").
 */
public class Bridge extends SysMLUtil {

	private final File library;
	private boolean isLibraryLoaded = false;
	private int textCount = 0;
	/** Inputs kept loaded (parse with keep), until unload. */
	private final Set<Resource> kept = java.util.Collections.newSetFromMap(new IdentityHashMap<>());

	Bridge(File library) {
		super();
		this.library = library;
		addExtension(".kerml");
		addExtension(".sysml");
		setVerbose(false);
	}

	// Setup

	static void setup(String mode, boolean tables, File library) throws Exception {
		EPackage.Registry.INSTANCE.put(SysMLPackage.eNS_URI, SysMLPackage.eINSTANCE);
		if (library != null) {
			SysMLLibraryUtil.setModelLibraryDirectory(library.getAbsolutePath() + "/");
		}
		switch (mode) {
		case "patched":
			// Read by the patched NameTables when it is initialized.
			System.setProperty("memo.tables", Boolean.toString(tables));
			System.setProperty("memo.tables.active", "true");
			Class<? extends IScopeProvider> kerml = Class.forName("org.omg.kerml.xtext.scoping.MemoKerMLScopeProvider")
					.asSubclass(IScopeProvider.class);
			Class<? extends IScopeProvider> sysml = Class.forName("org.omg.sysml.xtext.scoping.MemoSysMLScopeProvider")
					.asSubclass(IScopeProvider.class);
			new KerMLStandaloneSetup() {
				@Override
				public Injector createInjector() {
					return Guice.createInjector(override(new KerMLRuntimeModule(), kerml));
				}
			}.createInjectorAndDoEMFRegistration();
			new SysMLStandaloneSetup() {
				@Override
				public Injector createInjector() {
					return Guice.createInjector(override(new SysMLRuntimeModule(), sysml));
				}
			}.createInjectorAndDoEMFRegistration();
			break;
		case "userside":
			Set<String> components = new HashSet<>(Arrays.asList("delegates", "memo"));
			if (tables) {
				components.add("tables");
			}
			Class.forName("org.sysmlperf.UserSideSetup").getMethod("setup", Set.class).invoke(null, components);
			break;
		default:
			KerMLStandaloneSetup.doSetup();
			SysMLStandaloneSetup.doSetup();
		}
	}

	static Module override(Module runtimeModule, Class<? extends IScopeProvider> provider) {
		return Modules.override(runtimeModule).with(binder -> binder.bind(IScopeProvider.class).to(provider));
	}

	void loadLibrary() {
		if (!isLibraryLoaded && library != null) {
			readAll(library.getAbsolutePath(), false);
			isLibraryLoaded = true;
		}
	}

	void resolveLibrary() {
		loadLibrary();
		for (Resource resource: new ArrayList<>(getResourceSet().getResources())) {
			if (!isInputResource(resource)) {
				EcoreUtil.resolveAll(resource);
			}
		}
	}

	// Requests

	String handle(JsonObject request) throws Exception {
		String op = request.has("op")? request.get("op").getAsString(): "parse";
		StringWriter out = new StringWriter();
		JsonWriter w = new JsonWriter(out);
		w.beginObject();
		if (request.has("id")) {
			w.name("id").jsonValue(request.get("id").toString());
		}
		w.name("ok").value(true);
		switch (op) {
		case "parse":
			parse(request, w);
			break;
		case "get":
			get(request, w);
			break;
		case "metaclasses":
			writeMetaclasses(w);
			break;
		case "unload":
			w.name("unloaded").value(unloadInputs());
			break;
		case "load_library":
			long t = System.nanoTime();
			if (request.has("resolve") && request.get("resolve").getAsBoolean()) {
				resolveLibrary();
			} else {
				loadLibrary();
			}
			w.name("ms").value((System.nanoTime() - t) / 1e6);
			break;
		case "stats":
			w.name("resources").value(getResourceSet().getResources().size());
			w.name("inputs").value(getInputResources().size());
			w.name("libraryLoaded").value(isLibraryLoaded);
			Runtime runtime = Runtime.getRuntime();
			w.name("heapUsedMB").value((runtime.totalMemory() - runtime.freeMemory()) >> 20);
			break;
		default:
			throw new IllegalArgumentException("unknown op: " + op);
		}
		w.endObject();
		w.flush();
		return out.toString();
	}

	void parse(JsonObject request, JsonWriter w) throws Exception {
		String resolve = request.has("resolve")? request.get("resolve").getAsString(): "none";
		boolean validate = request.has("validate") && request.get("validate").getAsBoolean();
		boolean positions = !request.has("positions") || request.get("positions").getAsBoolean();
		boolean keep = request.has("keep") && request.get("keep").getAsBoolean();
		boolean uris = request.has("uris") && request.get("uris").getAsBoolean();
		List<String> derived = new ArrayList<>();
		if (request.has("derived")) {
			for (JsonElement name: request.getAsJsonArray("derived")) {
				derived.add(name.getAsString());
			}
		}
		boolean isResolving = !resolve.equals("none");

		long t0 = System.nanoTime();
		if (isResolving || validate || !derived.isEmpty()) {
			loadLibrary();
		}
		long t1 = System.nanoTime();
		List<Resource> resources = new ArrayList<>();
		for (JsonElement input: request.getAsJsonArray("inputs")) {
			resources.add(load(input.getAsJsonObject()));
		}
		long t2 = System.nanoTime();
		for (Resource resource: resources) {
			if (resolve.equals("lazy")) {
				EcoreUtil2.resolveLazyCrossReferences(resource, CancelIndicator.NullImpl);
			} else if (resolve.equals("all")) {
				EcoreUtil.resolveAll(resource);
			}
		}
		long t3 = System.nanoTime();
		List<List<Issue>> issues = new ArrayList<>();
		for (Resource resource: resources) {
			issues.add(validate? ((XtextResource)resource).getResourceServiceProvider().getResourceValidator()
					.validate(resource, CheckMode.ALL, CancelIndicator.NullImpl): null);
		}
		long t4 = System.nanoTime();

		w.name("models").beginArray();
		for (int i = 0; i < resources.size(); i++) {
			export(resources.get(i), issues.get(i), isResolving, positions, uris, derived, w);
		}
		w.endArray();
		long t5 = System.nanoTime();
		w.name("timing").beginObject()
			.name("library").value((t1 - t0) / 1e6)
			.name("parse").value((t2 - t1) / 1e6)
			.name("resolve").value((t3 - t2) / 1e6)
			.name("validate").value((t4 - t3) / 1e6)
			.name("export").value((t5 - t4) / 1e6)
			.endObject();
		if (keep) {
			kept.addAll(resources);
		} else {
			List<Resource> loaded = new ArrayList<>(resources);
			loaded.removeAll(kept);
			unload(loaded);
		}
	}

	Resource load(JsonObject input) throws IOException {
		Resource resource;
		if (input.has("path")) {
			String path = new File(input.get("path").getAsString()).getAbsolutePath();
			resource = getResourceSet().getResource(URI.createFileURI(path), true);
		} else {
			String name = input.has("name")? input.get("name").getAsString(): "input.sysml";
			resource = getResourceSet().createResource(URI.createURI("text:/" + (++textCount) + "/" + name));
			if (resource == null) {
				throw new IllegalArgumentException("unsupported file extension: " + name);
			}
			resource.load(new ByteArrayInputStream(input.get("text").getAsString().getBytes(StandardCharsets.UTF_8)),
					getResourceSet().getLoadOptions());
		}
		if (!isInputResource(resource)) {
			addInputResource(resource);
			// Indexed, so that inputs can reference each other's top-level elements.
			addResourceToIndex(resource);
		}
		return resource;
	}

	/** Unloads all inputs, including the kept ones. */
	int unloadInputs() {
		kept.clear();
		return unload(new ArrayList<>(getInputResources()));
	}

	int unload(List<Resource> resources) {
		ResourceDescriptionsData index = ResourceDescriptionsData.ResourceSetAdapter.findResourceDescriptionsData(getResourceSet());
		for (Resource resource: resources) {
			if (index != null) {
				index.removeDescription(resource.getURI());
			}
			resource.unload();
			getResourceSet().getResources().remove(resource);
			getInputResources().remove(resource);
		}
		return resources.size();
	}

	// Export

	void export(Resource resource, List<Issue> issues, boolean isResolved, boolean positions, boolean uris,
			List<String> derived, JsonWriter w) throws IOException {
		Map<EObject, Integer> ids = new IdentityHashMap<>();
		List<EObject> objects = new ArrayList<>();
		for (Iterator<EObject> contents = resource.getAllContents(); contents.hasNext();) {
			EObject object = contents.next();
			ids.put(object, objects.size());
			objects.add(object);
		}
		Map<EObject, String> qualifiedNames = new IdentityHashMap<>();

		w.beginObject();
		w.name("uri").value(resource.getURI().toString());
		w.name("roots").beginArray();
		for (EObject root: resource.getContents()) {
			w.value(ids.get(root));
		}
		w.endArray();
		w.name("elements").beginArray();
		for (EObject object: objects) {
			writeElement(object, ids, qualifiedNames, isResolved, positions, uris, derived, w);
		}
		w.endArray();

		w.name("issues").beginArray();
		if (issues != null) {
			for (Issue issue: issues) {
				w.beginObject();
				w.name("severity").value(issue.getSeverity().name().toLowerCase());
				w.name("message").value(issue.getMessage());
				w.name("line").value(issue.getLineNumber());
				w.name("column").value(issue.getColumn());
				if (issue.getCode() != null) {
					w.name("code").value(issue.getCode());
				}
				if (issue.getUriToProblem() != null && issue.getUriToProblem().fragment() != null) {
					EObject problem = resource.getEObject(issue.getUriToProblem().fragment());
					if (problem != null && ids.containsKey(problem)) {
						w.name("element").value(ids.get(problem));
					}
				}
				w.endObject();
			}
		} else {
			writeDiagnostics(resource.getErrors(), "error", w);
			writeDiagnostics(resource.getWarnings(), "warning", w);
		}
		w.endArray();
		w.endObject();
	}

	/**
	 * Writes elements given by URI (for example, of the library), each as a model with the element as its root
	 * (and, with subtree, all the elements it owns), as the export does; elements outside it are written as
	 * references by URI. Their references are resolved first.
	 */
	void get(JsonObject request, JsonWriter w) throws IOException {
		boolean subtree = request.has("subtree") && request.get("subtree").getAsBoolean();
		boolean positions = !request.has("positions") || request.get("positions").getAsBoolean();
		List<String> derived = new ArrayList<>();
		if (request.has("derived")) {
			for (JsonElement name: request.getAsJsonArray("derived")) {
				derived.add(name.getAsString());
			}
		}
		loadLibrary();
		Map<EObject, String> qualifiedNames = new IdentityHashMap<>();
		w.name("models").beginArray();
		for (JsonElement uri: request.getAsJsonArray("uris")) {
			// Only in loaded models (inputs kept and the library): loading a file here would leave it loaded, unlike
			// the inputs, and a later parse would reuse it.
			EObject root = getResourceSet().getEObject(URI.createURI(uri.getAsString()), false);
			if (root == null || root.eIsProxy()) {
				w.beginObject().name("uri").value(uri.getAsString()).name("missing").value(true).endObject();
				continue;
			}
			Map<EObject, Integer> ids = new IdentityHashMap<>();
			List<EObject> objects = new ArrayList<>();
			ids.put(root, 0);
			objects.add(root);
			if (subtree) {
				for (Iterator<EObject> contents = root.eAllContents(); contents.hasNext();) {
					EObject object = contents.next();
					ids.put(object, objects.size());
					objects.add(object);
				}
			}
			for (EObject object: objects) {
				resolveReferences(object);
			}
			w.beginObject();
			w.name("uri").value(root.eResource().getURI().toString());
			// Its owner, skipping a membership (written as "membership"), as a reference by URI.
			EObject container = root.eContainer();
			if (container instanceof OwningMembership) {
				w.name("membership").value(container.eClass().getName());
				container = container.eContainer();
			}
			if (container != null) {
				w.name("owner");
				writeTarget(container, w, ids, qualifiedNames, true);
			}
			w.name("roots").beginArray().value(0).endArray();
			w.name("elements").beginArray();
			for (EObject object: objects) {
				writeElement(object, ids, qualifiedNames, true, positions, true, derived, w);
			}
			w.endArray();
			w.name("issues").beginArray().endArray();
			w.endObject();
		}
		w.endArray();
	}

	/** Resolves the (non-derived) references of an object, as the linker does. */
	static void resolveReferences(EObject object) {
		for (EReference reference: object.eClass().getEAllReferences()) {
			if (reference.isContainment() || reference.isContainer() || reference.isDerived() ||
					reference == SysMLPackage.Literals.RELATIONSHIP__SOURCE ||
					reference == SysMLPackage.Literals.RELATIONSHIP__TARGET) {
				continue;
			}
			Object value = object.eGet(reference, true);
			if (value instanceof List) {
				for (Iterator<?> values = ((List<?>)value).iterator(); values.hasNext();) {
					values.next();
				}
			}
		}
	}

	/** The metaclasses of SysML (and KerML), with all their supertypes (from the most general one). */
	static void writeMetaclasses(JsonWriter w) throws IOException {
		w.name("metaclasses").beginObject();
		for (EClassifier classifier: SysMLPackage.eINSTANCE.getEClassifiers()) {
			if (classifier instanceof EClass) {
				EClass eClass = (EClass)classifier;
				w.name(eClass.getName()).beginObject();
				w.name("abstract").value(eClass.isAbstract());
				w.name("supertypes").beginArray();
				for (EClass supertype: eClass.getEAllSuperTypes()) {
					w.value(supertype.getName());
				}
				w.endArray();
				w.endObject();
			}
		}
		w.endObject();
	}

	void writeElement(EObject object, Map<EObject, Integer> ids, Map<EObject, String> qualifiedNames,
			boolean isResolved, boolean positions, boolean uris, List<String> derived, JsonWriter w)
			throws IOException {
		w.beginObject();
		w.name("id").value(ids.get(object));
		w.name("type").value(object.eClass().getName());
		if (uris) {
			w.name("fragment").value(object.eResource().getURIFragment(object));
		}
		EObject container = object.eContainer();
		if (container != null && ids.containsKey(container)) {
			w.name("owner").value(ids.get(container));
			w.name("role").value(object.eContainingFeature().getName());
		}
		if (positions) {
			ICompositeNode node = NodeModelUtils.getNode(object);
			if (node != null) {
				w.name("line").value(node.getStartLine());
				w.name("endLine").value(node.getEndLine());
				w.name("offset").value(node.getOffset());
				w.name("length").value(node.getLength());
			}
		}
		for (EAttribute attribute: object.eClass().getEAllAttributes()) {
			if (attribute.isDerived() || attribute.isTransient() || !object.eIsSet(attribute) ||
					attribute == SysMLPackage.Literals.ELEMENT__ELEMENT_ID) {
				continue;
			}
			w.name(attribute.getName());
			writeValue(object.eGet(attribute), w, ids, qualifiedNames, isResolved);
		}
		boolean hasReferences = false;
		for (EReference reference: object.eClass().getEAllReferences()) {
			if (reference.isContainment() || reference.isContainer() || reference.isDerived() ||
					// Computed from other references (and resolving them) by each kind of Relationship.
					reference == SysMLPackage.Literals.RELATIONSHIP__SOURCE ||
					reference == SysMLPackage.Literals.RELATIONSHIP__TARGET) {
				continue;
			}
			Object value = object.eGet(reference, false);
			List<?> values = reference.isMany()? basicList(value): value == null? List.of(): List.of(value);
			if (values.isEmpty()) {
				continue;
			}
			List<INode> nodes = null;
			boolean hasProxies = false;
			for (Object target: values) {
				hasProxies |= ((EObject)target).eIsProxy();
			}
			if (hasProxies) {
				nodes = NodeModelUtils.findNodesForFeature(object, reference);
				if (nodes.isEmpty()) {
					// Not parsed for this reference: computed from another one (e.g., the general of a
					// FeatureTyping is its type), which is written itself.
					continue;
				}
			}
			if (!hasReferences) {
				w.name("refs").beginObject();
				hasReferences = true;
			}
			w.name(reference.getName()).beginArray();
			for (int i = 0; i < values.size(); i++) {
				EObject target = (EObject)values.get(i);
				if (target.eIsProxy()) {
					w.beginObject().name("unresolved")
						.value(i < nodes.size()? NodeModelUtils.getTokenText(nodes.get(i)): null).endObject();
				} else {
					writeTarget(target, w, ids, qualifiedNames, isResolved);
				}
			}
			w.endArray();
		}
		if (hasReferences) {
			w.endObject();
		}
		if (!derived.isEmpty()) {
			w.name("derived").beginObject();
			for (String name: derived) {
				EStructuralFeature feature = object.eClass().getEStructuralFeature(name);
				if (feature != null) {
					w.name(name);
					writeValue(object.eGet(feature), w, ids, qualifiedNames, isResolved);
				}
			}
			w.endObject();
		}
		w.endObject();
	}

	static List<?> basicList(Object value) {
		return value instanceof InternalEList? ((InternalEList<?>)value).basicList(): (List<?>)value;
	}

	void writeValue(Object value, JsonWriter w, Map<EObject, Integer> ids, Map<EObject, String> qualifiedNames,
			boolean isResolved) throws IOException {
		if (value instanceof Collection) {
			w.beginArray();
			for (Object item: (Collection<?>)value) {
				writeValue(item, w, ids, qualifiedNames, isResolved);
			}
			w.endArray();
		} else if (value instanceof EObject) {
			// Unlike in refs, an element of the same model is {"id": n}, as values can also be numbers.
			Integer id = ids.get(value);
			if (id != null) {
				w.beginObject().name("id").value(id).endObject();
			} else {
				writeTarget((EObject)value, w, ids, qualifiedNames, isResolved);
			}
		} else if (value == null) {
			w.nullValue();
		} else if (value instanceof Boolean) {
			w.value((Boolean)value);
		} else if (value instanceof Number) {
			w.value((Number)value);
		} else {
			w.value(value.toString());
		}
	}

	/**
	 * A reference to an element of the same model is its id; to another model (such as the library), an object
	 * with its URI and, if resolved, its qualified name.
	 */
	void writeTarget(EObject target, JsonWriter w, Map<EObject, Integer> ids, Map<EObject, String> qualifiedNames,
			boolean isResolved) throws IOException {
		Integer id = ids.get(target);
		if (id != null) {
			w.value(id);
			return;
		}
		w.beginObject();
		w.name("uri").value(EcoreUtil.getURI(target).toString());
		if (isResolved && target instanceof Element && !target.eIsProxy()) {
			// A membership (e.g. imported) has no name: its member's qualified name, and its member's URI.
			Element member = target instanceof Membership? ((Membership)target).getMemberElement(): null;
			String qualifiedName = qualifiedNames.computeIfAbsent(target, t -> t instanceof Membership?
					member == null? null: member.getQualifiedName(): ((Element)t).getQualifiedName());
			if (qualifiedName != null) {
				w.name("qualifiedName").value(qualifiedName);
			}
			if (member != null && !member.eIsProxy()) {
				w.name("memberUri").value(EcoreUtil.getURI(member).toString());
			}
		}
		w.endObject();
	}

	static void writeDiagnostics(EList<Resource.Diagnostic> diagnostics, String severity, JsonWriter w) throws IOException {
		for (Resource.Diagnostic diagnostic: diagnostics) {
			w.beginObject();
			w.name("severity").value(severity);
			w.name("message").value(diagnostic.getMessage());
			w.name("line").value(diagnostic.getLine());
			w.name("column").value(diagnostic.getColumn());
			w.endObject();
		}
	}

	// Main

	public static void main(String[] args) throws Exception {
		PrintStream protocol = new PrintStream(new FileOutputStream(FileDescriptor.out), true, StandardCharsets.UTF_8);
		System.setOut(System.err); // Anything else printed goes to stderr.

		String mode = "patched";
		File library = null;
		boolean tables = false, preload = false, preresolve = false;
		for (int i = 0; i < args.length; i++) {
			switch (args[i]) {
			case "--library": library = new File(args[++i]).getCanonicalFile(); break;
			case "--mode": mode = args[++i]; break;
			case "--tables": tables = true; break;
			case "--preload": preload = true; break;
			case "--preresolve": preresolve = true; break;
			default: throw new IllegalArgumentException("unknown option: " + args[i]);
			}
		}
		long t0 = System.nanoTime();
		setup(mode, tables, library);
		Bridge bridge = new Bridge(library);
		long t1 = System.nanoTime();
		if (preresolve) {
			bridge.resolveLibrary();
		} else if (preload) {
			bridge.loadLibrary();
		}
		long t2 = System.nanoTime();
		JsonObject ready = new JsonObject();
		ready.addProperty("ready", true);
		ready.addProperty("mode", mode);
		ready.addProperty("setupMs", (t1 - t0) / 1e6);
		ready.addProperty("libraryMs", (t2 - t1) / 1e6);
		protocol.println(ready);

		BufferedReader in = new BufferedReader(new InputStreamReader(System.in, StandardCharsets.UTF_8));
		for (String line; (line = in.readLine()) != null;) {
			if (line.isBlank()) {
				continue;
			}
			JsonObject request = null;
			String response;
			try {
				request = JsonParser.parseString(line).getAsJsonObject();
				if (request.has("op") && request.get("op").getAsString().equals("shutdown")) {
					break;
				}
				response = bridge.handle(request);
			} catch (Throwable e) {
				JsonObject error = new JsonObject();
				if (request != null && request.has("id")) {
					error.add("id", request.get("id"));
				}
				error.addProperty("ok", false);
				error.addProperty("error", e.toString());
				JsonArray trace = new JsonArray();
				for (StackTraceElement element: e.getStackTrace()) {
					trace.add(element.toString());
					if (trace.size() >= 20) break;
				}
				error.add("trace", trace);
				response = error.toString();
				try {
					bridge.unloadInputs();
				} catch (Throwable ignored) {
				}
			}
			protocol.println(response);
		}
		protocol.flush();
		System.exit(0);
	}
}
