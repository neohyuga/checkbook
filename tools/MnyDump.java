// One-time helper: dump every table of a Microsoft Money (.mny) file to CSV.
//
// Usage:  java -cp "lib/*" MnyDump.java <file.mny> <output-dir>
// The password is read from standard input (one line; empty line = no password)
// so that it never appears on the command line / process list.

import com.healthmarketscience.jackcess.*;
import com.healthmarketscience.jackcess.crypt.CryptCodecProvider;

import java.io.*;
import java.nio.charset.StandardCharsets;
import java.nio.file.*;
import java.util.*;

public class MnyDump {
    public static void main(String[] args) throws Exception {
        if (args.length != 2) {
            System.err.println("usage: MnyDump <file.mny> <output-dir>");
            System.exit(2);
        }
        File mny = new File(args[0]);
        Path out = Paths.get(args[1]);
        Files.createDirectories(out);

        BufferedReader in = new BufferedReader(new InputStreamReader(System.in, StandardCharsets.UTF_8));
        String pw = in.readLine();
        if (pw == null) pw = "";

        Database db = open(mny, pw);
        if (db == null && !pw.equals(pw.toUpperCase())) {
            // Money passwords are case-insensitive in some versions; retry upper-case.
            db = open(mny, pw.toUpperCase());
        }
        if (db == null) {
            System.err.println("ERROR: could not open file (wrong password?)");
            System.exit(3);
        }

        try (Database d = db) {
            for (String name : d.getTableNames()) {
                Table t = d.getTable(name);
                Path csv = out.resolve(name + ".csv");
                try (Writer w = Files.newBufferedWriter(csv, StandardCharsets.UTF_8)) {
                    List<String> cols = new ArrayList<>();
                    for (Column c : t.getColumns()) cols.add(c.getName());
                    writeRow(w, cols);
                    int n = 0;
                    for (Row r : t) {
                        List<String> vals = new ArrayList<>();
                        for (String c : cols) {
                            Object v = r.get(c);
                            vals.add(v == null ? "" : v.toString());
                        }
                        writeRow(w, vals);
                        n++;
                    }
                    System.out.println(name + ": " + n + " rows");
                }
            }
        }
    }

    static Database open(File f, String pw) {
        try {
            return new DatabaseBuilder(f)
                    .setReadOnly(true)
                    .setCodecProvider(new CryptCodecProvider(pw))
                    .open();
        } catch (Exception e) {
            return null;
        }
    }

    static void writeRow(Writer w, List<String> vals) throws IOException {
        StringBuilder sb = new StringBuilder();
        for (int i = 0; i < vals.size(); i++) {
            if (i > 0) sb.append(',');
            String v = vals.get(i);
            sb.append('"').append(v.replace("\"", "\"\"")).append('"');
        }
        sb.append("\r\n");
        w.write(sb.toString());
    }
}
