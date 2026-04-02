#!/bin/bash -eu

cd $SRC/mock_java
CURRENT_VERSION=$(mvn org.apache.maven.plugins:maven-help-plugin:3.2.0:evaluate \
-Dexpression=project.version -q -DforceStdout)
PROJECT_JARS=mock_java.jar
mvn package -Dlicense.skip=true
cp "target/mock_java-$CURRENT_VERSION.jar" $OUT/$PROJECT_JARS

mvn dependency:copy -Dartifact=com.google.code.gson:gson:2.8.6 -DoutputDirectory=$OUT/

FUZZER_JARS=gson-2.8.6.jar

ALL_JARS="$PROJECT_JARS $FUZZER_JARS"
BUILD_CLASSPATH=$(echo $ALL_JARS | xargs printf -- "$OUT/%s:"):$JAZZER_API_PATH

RUNTIME_CLASSPATH=$(echo $ALL_JARS | xargs printf -- "\$this_dir/%s:"):.:\$this_dir

FUZZ_DIR=$SRC/fuzz
for fuzzer in $(find $FUZZ_DIR -name '*.java'); do
  fuzzer_basename=$(basename -s .java $fuzzer)
  javac -cp $BUILD_CLASSPATH $fuzzer
  cp $FUZZ_DIR/$fuzzer_basename.class $OUT/

  echo "#!/bin/bash
this_dir=\$(dirname \"\$0\")
if [[ \"\$@\" =~ (^| )-runs=[0-9]+($| ) ]]; then
  mem_settings='-Xmx1900m:-Xss900k'
else
  mem_settings='-Xmx2048m:-Xss1024k'
fi
LD_LIBRARY_PATH=\"$JVM_LD_LIBRARY_PATH\":\$this_dir \
\$this_dir/jazzer_driver --agent_path=\$this_dir/jazzer_agent_deploy.jar \
--cp=$RUNTIME_CLASSPATH \
--target_class=$fuzzer_basename \
--jvm_args=\"\$mem_settings\" \
\$@" > $OUT/$fuzzer_basename
  chmod +x $OUT/$fuzzer_basename
done

cp -r "$SRC/mock_java" "$OUT/mock_java"
