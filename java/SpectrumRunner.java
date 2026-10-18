import java.io.*;
import java.nio.file.*;
import java.util.*;
import org.junit.runner.*;
import org.junit.runner.notification.*;
import org.jacoco.agent.rt.RT;
import org.jacoco.cli.internal.core.analysis.*;
import org.jacoco.cli.internal.core.data.*;

/** Execute the suite with per-test coverage, retaining JUnit class lifecycle semantics. */
public final class SpectrumRunner {
    private static File classes;
    private static PrintWriter spectra;
    private static final Map<String, byte[]> classBytes = new TreeMap<>();
    private static String locations(byte[] data, boolean all) throws IOException {
        ExecutionDataStore store = new ExecutionDataStore();
        ExecutionDataReader reader = new ExecutionDataReader(new ByteArrayInputStream(data));
        reader.setExecutionDataVisitor(store);
        reader.setSessionInfoVisitor(new SessionInfoStore());
        while (reader.read()) { }
        CoverageBuilder coverage = new CoverageBuilder();
        Analyzer analyzer = new Analyzer(store, coverage);
        if (all) {
            for (Map.Entry<String, byte[]> item : classBytes.entrySet()) {
                analyzer.analyzeClass(item.getValue(), item.getKey());
            }
        } else {
            for (ExecutionData item : store.getContents()) {
                if (item.hasHits() && classBytes.containsKey(item.getName())) {
                    analyzer.analyzeClass(classBytes.get(item.getName()), item.getName());
                }
            }
        }
        TreeSet<String> lines = new TreeSet<>();
        for (IClassCoverage c : coverage.getClasses()) {
            String source = (c.getPackageName().isEmpty() ? "" : c.getPackageName() + "/")
                + c.getSourceFileName();
            for (int line = c.getFirstLine(); line <= c.getLastLine(); line++) {
                ILine item = c.getLine(line);
                if (all ? item.getInstructionCounter().getTotalCount() > 0
                        : item.getInstructionCounter().getCoveredCount() > 0) {
                    lines.add(source + ":" + line);
                }
            }
        }
        return String.join(",", lines);
    }
    public static void main(String[] args) throws Exception {
        classes = new File(args[0]);
        try (java.util.stream.Stream<Path> files = Files.walk(classes.toPath())) {
            Iterator<Path> paths = files.filter(p -> p.toString().endsWith(".class")).iterator();
            while (paths.hasNext()) {
                Path file = paths.next();
                String name = classes.toPath().relativize(file).toString().replace('\\', '/');
                classBytes.put(name.substring(0, name.length() - 6), Files.readAllBytes(file));
            }
        }
        spectra = new PrintWriter(args[2], "UTF-8");
        if (args.length < 4 || !args[3].equals("no-universe")) {
            spectra.println("UNIVERSE\t0\t" + locations(new byte[0], true));
        }
        JUnitCore core = new JUnitCore();
        core.addListener(new RunListener() {
            private boolean failed;
            @Override public void testStarted(Description d) { failed = false; }
            @Override public void testFailure(Failure f) { failed = true; }
            @Override public void testFinished(Description d) throws Exception {
                String id = d.getClassName() + "::" + d.getMethodName();
                spectra.println(id + "\t" + (failed ? "1" : "0") + "\t"
                    + locations(RT.getAgent().getExecutionData(true), false));
                spectra.flush();
            }
        });
        int failures = 0;
        for (String name : Files.readAllLines(Paths.get(args[1]))) {
            if (name.trim().isEmpty()) continue;
            Class<?> testClass = Class.forName(name.trim());
            Result result = core.run(testClass);
            failures += result.getFailureCount();
            for (Failure failure : result.getFailures()) {
                System.out.println(failure.getTestHeader());
                System.out.println(failure.getTrace());
            }
        }
        spectra.close();
        System.out.println("SPECTRUM_FAILURES=" + failures);
        System.out.flush();
        System.exit(0);
    }
}
