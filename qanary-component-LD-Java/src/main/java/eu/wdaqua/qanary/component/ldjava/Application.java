package eu.wdaqua.qanary.component.ldjava;

import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.boot.SpringApplication;
import org.springframework.boot.autoconfigure.SpringBootApplication;
import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.ComponentScan;

import eu.wdaqua.qanary.component.QanaryComponent;
import eu.wdaqua.qanary.component.QanaryComponentConfiguration;

/**
 * Spring Boot entry point for the Language-Detection Java component. The
 * {@link QanaryComponent} bean is picked up by the QanaryServiceController in the
 * qa.component framework, which exposes /annotatequestion and registers the
 * component with the Qanary pipeline (Spring Boot Admin).
 */
@SpringBootApplication
@ComponentScan(basePackages = {"eu.wdaqua.qanary"})
public class Application {

    @Autowired
    public QanaryComponentConfiguration qanaryComponentConfiguration;

    @Bean
    public QanaryComponent qanaryComponent(
            @Value("${spring.application.name}") final String applicationName) {
        return new LanguageDetector(applicationName);
    }

    public static void main(String[] args) {
        SpringApplication.run(Application.class, args);
    }
}
